"""
api.py — API Flask pour le dashboard Padel Stats.
Routes :
  GET /                              → sert le HTML
  GET /api/search?q=                 → recherche joueurs
  GET /api/player/<id>               → profil complet
  GET /api/suggest/<id>              → suggestions partenaires
  GET /api/path/<src>/<tgt>          → degrés de séparation
  GET /api/ego/<id>?depth=2          → graphe ego
  GET /api/stats                     → stats globales dashboard
  GET /api/leaderboard               → classement paginé + filtres
  GET /api/movers?sexe=H&n=8         → hausse/baisse classements
  GET /api/clubs?top=100             → top clubs
  GET /api/health                    → santé
"""
import os
import re
import sys
import datetime
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))

from flask import Flask, jsonify, request, send_file
from flask_cors import CORS

from graph_engine import engine
from player_profile import search_players, get_player_profile
from suggester import suggest_partners
from db import USE_POSTGRES
from auth import create_magic_link, verify_token, get_user_from_session, invalidate_session
from user_data import (
    link_player, unlink_player, update_display_name,
    get_favorites, add_favorite, remove_favorite, is_favorite,
)

app = Flask(__name__)
CORS(app)

# Créer les index manquants au démarrage (idempotent, ~5s si absent, ~0ms si déjà présent)
from db import ensure_indexes as _ensure_indexes
_ensure_indexes()

# Graphe chargé à la demande uniquement (lazy) — le preload au démarrage
# dépasse le statement_timeout de 90s sur le free tier Render → crash systématique.
# Le chargement se fera lors du premier appel à /api/graph/* ou /api/ego/*.


# ── Plus de préchauffage in-memory : trop lourd pour le free tier ────────────
# Les caches sont maintenant alimentés par le script externe `precompute.py`
# (à lancer manuellement OU via /api/admin/precompute) qui stocke les résultats
# dans la table cache_responses (lue par _try_precomputed dans chaque route).


@app.get("/api/home")
def route_home_data():
    """Données légères pour la page d'accueil : compteurs + top 5 H/F."""
    from db import fetchall, fetchone
    counts = fetchone(
        "SELECT "
        "(SELECT COUNT(*) FROM joueurs WHERE classement IS NOT NULL AND sexe='H') AS nb_h, "
        "(SELECT COUNT(*) FROM joueurs WHERE classement IS NOT NULL AND sexe='F') AS nb_f, "
        "(SELECT COUNT(*) FROM clubs) AS nb_clubs, "
        "(SELECT COUNT(DISTINCT id_tournoi) FROM participations) AS nb_tournois"
    ) or {}
    def top(sexe):
        return fetchall(
            "SELECT id_fft, nom, prenom, classement, points, club_nom, comite "
            "FROM joueurs WHERE sexe=? AND classement IS NOT NULL "
            "ORDER BY classement ASC LIMIT 5", (sexe,))
    def _one(q):
        try:
            return fetchone(q)
        except Exception:
            return None
    ex_j = _one("SELECT j.id_fft AS id_fft, j.nom AS nom, j.prenom AS prenom, j.classement AS classement, j.sexe AS sexe FROM joueurs j JOIN (SELECT id_joueur, COUNT(*) AS n FROM participations GROUP BY id_joueur HAVING COUNT(*)>=5) cnt ON cnt.id_joueur=j.id_fft WHERE j.classement IS NOT NULL AND j.classement<=20000 ORDER BY RANDOM() LIMIT 1")
    ex_c = _one("SELECT club_nom, COUNT(*) AS n FROM joueurs WHERE club_nom IS NOT NULL AND club_nom!='' GROUP BY club_nom HAVING COUNT(*)>=15 ORDER BY RANDOM() LIMIT 1")
    ex_t = _one("SELECT tr.id_tournoi, t.nom, tr.niveau_effectif AS niv, ROUND(tr.indice_niveau) AS ind, tr.sexe FROM tournois_rating tr JOIN tournois t ON t.id_tournoi=tr.id_tournoi WHERE tr.equipes=0 AND tr.multi_board=0 AND tr.nb_paires>=12 AND tr.niveau_effectif>=250 ORDER BY RANDOM() LIMIT 1")
    examples = {
        "joueur": ({"id": ex_j["id_fft"], "nom_complet": f"{(ex_j.get('prenom') or '').strip()} {(ex_j.get('nom') or '').strip()}".strip(),
                    "classement": ex_j["classement"], "sexe": ex_j["sexe"]} if ex_j else None),
        "club": ({"nom": ex_c["club_nom"], "nb": int(ex_c["n"] or 0)} if ex_c else None),
        "tournoi": ({"id": ex_t["id_tournoi"], "nom": ex_t["nom"], "niveau": ex_t["niv"],
                     "indice": (int(ex_t["ind"]) if ex_t["ind"] is not None else None), "sexe": ex_t["sexe"]} if ex_t else None),
    }
    deltas = {}
    try:
        import json as _json
        _tp = os.path.join(os.path.dirname(__file__), "timeline.json")
        if os.path.exists(_tp):
            _tl = _json.load(open(_tp, encoding="utf-8"))
            _h, _f, _mo = _tl.get("licencies_h") or [], _tl.get("licencies_f") or [], _tl.get("months") or []
            if len(_h) >= 2 and len(_f) >= 2:
                deltas = {"h": _h[-1] - _h[-2], "f": _f[-1] - _f[-2], "mois": (_mo[-2] if len(_mo) >= 2 else "")}
    except Exception:
        deltas = {}
    return jsonify({"counts": dict(counts), "top_h": top("H"), "top_f": top("F"), "examples": examples, "deltas": deltas})


@app.get("/api/example/<kind>")
def route_example(kind):
    """Un exemple aléatoire (joueur / club / tournoi) pour la vitrine de la home."""
    from db import fetchone
    try:
        if kind == "joueur":
            r = fetchone("SELECT j.id_fft AS id_fft, j.nom AS nom, j.prenom AS prenom, j.classement AS classement, j.sexe AS sexe FROM joueurs j JOIN (SELECT id_joueur, COUNT(*) AS n FROM participations GROUP BY id_joueur HAVING COUNT(*)>=5) cnt ON cnt.id_joueur=j.id_fft WHERE j.classement IS NOT NULL AND j.classement<=20000 ORDER BY RANDOM() LIMIT 1")
            return jsonify({"id": r["id_fft"], "nom_complet": f"{(r.get('prenom') or '').strip()} {(r.get('nom') or '').strip()}".strip(), "classement": r["classement"], "sexe": r["sexe"]} if r else None)
        if kind == "club":
            r = fetchone("SELECT club_nom, COUNT(*) AS n FROM joueurs WHERE club_nom IS NOT NULL AND club_nom!='' GROUP BY club_nom HAVING COUNT(*)>=15 ORDER BY RANDOM() LIMIT 1")
            return jsonify({"nom": r["club_nom"], "nb": int(r["n"] or 0)} if r else None)
        if kind == "tournoi":
            r = fetchone("SELECT tr.id_tournoi, t.nom, tr.niveau_effectif AS niv, ROUND(tr.indice_niveau) AS ind, tr.sexe FROM tournois_rating tr JOIN tournois t ON t.id_tournoi=tr.id_tournoi WHERE tr.equipes=0 AND tr.multi_board=0 AND tr.nb_paires>=12 AND tr.niveau_effectif>=250 ORDER BY RANDOM() LIMIT 1")
            return jsonify({"id": r["id_tournoi"], "nom": r["nom"], "niveau": r["niv"], "indice": (int(r["ind"]) if r["ind"] is not None else None), "sexe": r["sexe"]} if r else None)
    except Exception:
        pass
    return jsonify(None)


@app.get("/joueur/random")
def route_random_joueur():
    """Redirige vers une fiche joueur au hasard (avec de vrais matchs)."""
    from flask import redirect
    from db import fetchone
    r = fetchone("SELECT j.id_fft AS id_fft FROM joueurs j JOIN (SELECT id_joueur, COUNT(*) AS n FROM participations GROUP BY id_joueur HAVING COUNT(*)>=5) cnt ON cnt.id_joueur=j.id_fft WHERE j.classement IS NOT NULL AND j.classement<=20000 ORDER BY RANDOM() LIMIT 1")
    return redirect(f"/joueur/{r['id_fft']}" if r else "/classement")


@app.get("/club/random")
def route_random_club():
    """Redirige vers une fiche club au hasard."""
    from flask import redirect
    from db import fetchone
    from urllib.parse import quote
    r = fetchone("SELECT club_nom FROM joueurs WHERE club_nom IS NOT NULL AND club_nom!='' GROUP BY club_nom HAVING COUNT(*)>=15 ORDER BY RANDOM() LIMIT 1")
    return redirect(f"/club?nom={quote(r['club_nom'])}" if r else "/clubs")


@app.get("/tournoi/random")
def route_random_tournoi():
    """Redirige vers une fiche tournoi au hasard."""
    from flask import redirect
    from db import fetchone
    r = fetchone("SELECT tr.id_tournoi FROM tournois_rating tr WHERE tr.equipes=0 AND tr.multi_board=0 AND tr.nb_paires>=12 AND tr.niveau_effectif>=250 ORDER BY RANDOM() LIMIT 1")
    return redirect(f"/tournoi/{r['id_tournoi']}" if r else "/tournois")


@app.get("/api/timeline")
def route_timeline():
    """Séries temporelles précalculées (licenciés/mois) pour la courbe 'explosion du padel'."""
    p = os.path.join(os.path.dirname(__file__), "timeline.json")
    if os.path.exists(p):
        return send_file(p, mimetype="application/json")
    return jsonify({"months": [], "licencies_h": [], "licencies_f": [], "total": []})


@app.get("/api/evolution")
def route_evolution():
    """Nouveaux classés du mois (total + par département), précalculé."""
    p = os.path.join(os.path.dirname(__file__), "evolution.json")
    if os.path.exists(p):
        return send_file(p, mimetype="application/json")
    return jsonify({"total": 0, "by_dept": {}})


@app.route("/")
def index():
    return send_file(os.path.join(os.path.dirname(__file__), "home.html"))


@app.route("/legacy")
def index_legacy():
    html_path = os.path.join(os.path.dirname(__file__), "..", "dashboard_mockup.html")
    return send_file(os.path.abspath(html_path))


@app.route("/joueur/<player_id>")
def route_fiche_page(player_id: str):
    """Sert la fiche joueur (v2). Les donnees sont chargees cote client via /api/player/<id>."""
    return send_file(os.path.join(os.path.dirname(__file__), "fiche.html"))


@app.get("/api/search")
def route_search():
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify([])
    limit = min(int(request.args.get("limit", 20)), 50)
    sexe = request.args.get("sexe", "").strip().upper() or None
    if sexe not in ("H", "F"):
        sexe = None
    return jsonify(search_players(q, limit=limit, sexe=sexe))


@app.get("/api/player/<player_id>")
def route_player(player_id: str):
    profile = get_player_profile(player_id)
    if not profile:
        return jsonify({"error": "Joueur introuvable"}), 404
    return jsonify(profile)


@app.get("/api/suggest/<player_id>")
def route_suggest(player_id: str):
    n = min(int(request.args.get("n", 10)), 30)
    return jsonify(suggest_partners(player_id, n=n))


import threading as _graph_th
_graph_load_lock = _graph_th.Lock()
_graph_loading = False

def _graph_ready():
    """Si le graphe n'est pas chargé : lance le chargement en tâche de fond (une seule fois)
    et renvoie 503 le temps que ça charge. La page front réessaie automatiquement."""
    global _graph_loading
    if engine._loaded:
        return None
    with _graph_load_lock:
        if not engine._loaded and not _graph_loading:
            _graph_loading = True
            def _bg_load():
                global _graph_loading
                try:
                    engine.load()
                except Exception as e:
                    print(f"[GraphEngine] chargement échoué : {e}")
                finally:
                    _graph_loading = False
            _graph_th.Thread(target=_bg_load, daemon=True, name="graph-load").start()
    return jsonify({"error": "graph_loading", "message": "Graphe en cours de chargement (1re fois, ~1 min)…"}), 503

@app.get("/api/path/<src_id>/<tgt_id>")
def route_path(src_id: str, tgt_id: str):
    err = _graph_ready()
    if err: return err
    result = engine.shortest_path(src_id, tgt_id)
    if result is None:
        return jsonify({"error": "Aucun chemin trouve"}), 404
    return jsonify(result)


@app.get("/api/ego/<player_id>")
def route_ego(player_id: str):
    err = _graph_ready()
    if err: return err
    depth = min(int(request.args.get("depth", 2)), 3)
    graph_data = engine.ego_graph(player_id, depth=depth)
    if not graph_data["nodes"]:
        return jsonify({"error": "Joueur introuvable ou sans partenaires"}), 404
    return jsonify(graph_data)


# ── Cache des réponses pré-calculées ──────────────────────────────────────────
# Stratégie : un script externe `precompute.py` calcule les réponses lourdes
# (60-90s chacune) UNE FOIS et les stocke dans la table cache_responses.
# Les endpoints lisent juste cette table → réponse instantanée (~5ms).
# Backup : un petit cache mémoire 10 min pour éviter les hits DB inutiles.
from flask import Response as _FlaskResponse
import time as _time_mod
_MEM_CACHE: dict[str, dict] = {}  # key → {"body": bytes, "ts": float}
_MEM_TTL   = 600  # secondes


def _cached_response(body: bytes):
    return _FlaskResponse(body, mimetype="application/json")


def _try_precomputed(key: str):
    """Renvoie une Response si le body est en cache (mémoire ou table), sinon None."""
    # 1. Cache mémoire (rapide, sans hit DB)
    entry = _MEM_CACHE.get(key)
    if entry and (_time_mod.time() - entry["ts"]) < _MEM_TTL:
        return _cached_response(entry["body"])
    # 2. Cache table (rempli par precompute.py)
    from db import get_cached_body
    body_str = get_cached_body(key)
    if body_str:
        body_bytes = body_str.encode("utf-8")
        _MEM_CACHE[key] = {"body": body_bytes, "ts": _time_mod.time()}
        return _cached_response(body_bytes)
    return None


def _store_in_mem(key: str, body: bytes):
    """Met en cache mémoire seulement (pas DB) — utilisé en fallback."""
    _MEM_CACHE[key] = {"body": body, "ts": _time_mod.time()}


@app.get("/api/stats")
def route_stats():
    from db import fetchall, fetchone
    import sys as _sys, time as _t

    # 1) Cache (mémoire ou table cache_responses remplie par precompute.py)
    cached = _try_precomputed("stats")
    if cached is not None:
        return cached

    def _step(label, t0):
        # Log par étape avec flush — pour identifier la requête lente
        print(f"   [stats] {label}: {_t.time()-t0:.1f}s", flush=True)
        _sys.stdout.flush()

    current_year = datetime.date.today().year
    _t0 = _t.time()

    # Compteurs globaux (pour KPIs)
    counts = fetchone("""
        SELECT
          (SELECT COUNT(*) FROM joueurs)                              AS nb_joueurs,
          (SELECT COUNT(*) FROM joueurs WHERE classement IS NOT NULL) AS nb_joueurs_classes,
          (SELECT COUNT(*) FROM joueurs WHERE classement IS NOT NULL AND sexe='H') AS nb_joueurs_h,
          (SELECT COUNT(*) FROM joueurs WHERE classement IS NOT NULL AND sexe='F') AS nb_joueurs_f,
          (SELECT COUNT(DISTINCT id_tournoi) FROM participations)     AS nb_tournois,
          (SELECT COUNT(*) FROM participations)                       AS nb_participations
    """) or {}
    _step("counts", _t0); _t0 = _t.time()

    ranking = fetchone("""
        SELECT
          SUM(CASE WHEN classement <= 100 THEN 1 ELSE 0 END)                AS top100,
          SUM(CASE WHEN classement BETWEEN 101 AND 1000 THEN 1 ELSE 0 END)  AS c100_1k,
          SUM(CASE WHEN classement BETWEEN 1001 AND 5000 THEN 1 ELSE 0 END) AS c1k_5k,
          SUM(CASE WHEN classement BETWEEN 5001 AND 20000 THEN 1 ELSE 0 END) AS c5k_20k,
          SUM(CASE WHEN classement BETWEEN 20001 AND 40000 THEN 1 ELSE 0 END) AS c20k_40k,
          SUM(CASE WHEN classement BETWEEN 40001 AND 80000 THEN 1 ELSE 0 END) AS c40k_80k,
          SUM(CASE WHEN classement > 80000 THEN 1 ELSE 0 END)               AS c80kplus,
          SUM(CASE WHEN classement <= 100 AND sexe='H' THEN 1 ELSE 0 END)                AS top100_h,
          SUM(CASE WHEN classement BETWEEN 101 AND 1000 AND sexe='H' THEN 1 ELSE 0 END)  AS c100_1k_h,
          SUM(CASE WHEN classement BETWEEN 1001 AND 5000 AND sexe='H' THEN 1 ELSE 0 END) AS c1k_5k_h,
          SUM(CASE WHEN classement BETWEEN 5001 AND 20000 AND sexe='H' THEN 1 ELSE 0 END) AS c5k_20k_h,
          SUM(CASE WHEN classement BETWEEN 20001 AND 40000 AND sexe='H' THEN 1 ELSE 0 END) AS c20k_40k_h,
          SUM(CASE WHEN classement BETWEEN 40001 AND 80000 AND sexe='H' THEN 1 ELSE 0 END) AS c40k_80k_h,
          SUM(CASE WHEN classement > 80000 AND sexe='H' THEN 1 ELSE 0 END)               AS c80kplus_h,
          SUM(CASE WHEN classement <= 100 AND sexe='F' THEN 1 ELSE 0 END)                AS top100_f,
          SUM(CASE WHEN classement BETWEEN 101 AND 1000 AND sexe='F' THEN 1 ELSE 0 END)  AS c100_1k_f,
          SUM(CASE WHEN classement BETWEEN 1001 AND 5000 AND sexe='F' THEN 1 ELSE 0 END) AS c1k_5k_f,
          SUM(CASE WHEN classement BETWEEN 5001 AND 20000 AND sexe='F' THEN 1 ELSE 0 END) AS c5k_20k_f,
          SUM(CASE WHEN classement BETWEEN 20001 AND 40000 AND sexe='F' THEN 1 ELSE 0 END) AS c20k_40k_f,
          SUM(CASE WHEN classement BETWEEN 40001 AND 80000 AND sexe='F' THEN 1 ELSE 0 END) AS c40k_80k_f,
          SUM(CASE WHEN classement > 80000 AND sexe='F' THEN 1 ELSE 0 END)               AS c80kplus_f
        FROM joueurs WHERE classement IS NOT NULL
    """) or {}
    _step("ranking", _t0); _t0 = _t.time()

    # Pyramide âges — agrégée en SQL (évite 149k lignes en Python)
    _yr = current_year
    _agg_sql = f"""
        SELECT sexe,
          SUM(CASE WHEN ({_yr} - CAST(naissance AS INT)) < 18                        THEN 1 ELSE 0 END) AS b0,
          SUM(CASE WHEN ({_yr} - CAST(naissance AS INT)) BETWEEN 18 AND 25           THEN 1 ELSE 0 END) AS b1,
          SUM(CASE WHEN ({_yr} - CAST(naissance AS INT)) BETWEEN 26 AND 30           THEN 1 ELSE 0 END) AS b2,
          SUM(CASE WHEN ({_yr} - CAST(naissance AS INT)) BETWEEN 31 AND 35           THEN 1 ELSE 0 END) AS b3,
          SUM(CASE WHEN ({_yr} - CAST(naissance AS INT)) BETWEEN 36 AND 40           THEN 1 ELSE 0 END) AS b4,
          SUM(CASE WHEN ({_yr} - CAST(naissance AS INT)) BETWEEN 41 AND 45           THEN 1 ELSE 0 END) AS b5,
          SUM(CASE WHEN ({_yr} - CAST(naissance AS INT)) BETWEEN 46 AND 55           THEN 1 ELSE 0 END) AS b6,
          SUM(CASE WHEN ({_yr} - CAST(naissance AS INT)) BETWEEN 56 AND 65           THEN 1 ELSE 0 END) AS b7,
          SUM(CASE WHEN ({_yr} - CAST(naissance AS INT)) > 65                        THEN 1 ELSE 0 END) AS b8
        FROM joueurs
        WHERE naissance IS NOT NULL
          AND LENGTH(naissance) = 4
          AND sexe IN ('H','F')
        GROUP BY sexe
    """ if not USE_POSTGRES else f"""
        SELECT sexe,
          SUM(CASE WHEN ({_yr} - naissance::int) < 18                        THEN 1 ELSE 0 END) AS b0,
          SUM(CASE WHEN ({_yr} - naissance::int) BETWEEN 18 AND 25           THEN 1 ELSE 0 END) AS b1,
          SUM(CASE WHEN ({_yr} - naissance::int) BETWEEN 26 AND 30           THEN 1 ELSE 0 END) AS b2,
          SUM(CASE WHEN ({_yr} - naissance::int) BETWEEN 31 AND 35           THEN 1 ELSE 0 END) AS b3,
          SUM(CASE WHEN ({_yr} - naissance::int) BETWEEN 36 AND 40           THEN 1 ELSE 0 END) AS b4,
          SUM(CASE WHEN ({_yr} - naissance::int) BETWEEN 41 AND 45           THEN 1 ELSE 0 END) AS b5,
          SUM(CASE WHEN ({_yr} - naissance::int) BETWEEN 46 AND 55           THEN 1 ELSE 0 END) AS b6,
          SUM(CASE WHEN ({_yr} - naissance::int) BETWEEN 56 AND 65           THEN 1 ELSE 0 END) AS b7,
          SUM(CASE WHEN ({_yr} - naissance::int) > 65                        THEN 1 ELSE 0 END) AS b8
        FROM joueurs
        WHERE naissance IS NOT NULL
          AND naissance ~ '^[0-9]{{4}}$'
          AND sexe IN ('H','F')
        GROUP BY sexe
    """
    pyr_rows = fetchall(_agg_sql)
    pyramid = {"H": [0] * 9, "F": [0] * 9}
    for r in pyr_rows:
        s = r.get("sexe")
        if s in pyramid:
            pyramid[s] = [int(r.get(f"b{i}") or 0) for i in range(9)]
    _step("pyramide", _t0); _t0 = _t.time()

    # Mois du dernier snapshot classement (pour affichage dynamique)
    snapshot_mois_raw = fetchone(
        "SELECT MAX(classement_date) as m FROM joueurs WHERE classement_date IS NOT NULL"
    )
    snapshot_mois = (snapshot_mois_raw or {}).get("m") or ""  # ex: "2025-05"
    # Formatter "2025-05" → "Mai 2025"
    MONTH_FR = {'01':'Janvier','02':'Février','03':'Mars','04':'Avril','05':'Mai',
                '06':'Juin','07':'Juillet','08':'Août','09':'Septembre',
                '10':'Octobre','11':'Novembre','12':'Décembre'}
    snapshot_label = ""
    if snapshot_mois and len(snapshot_mois) == 7:
        y, m = snapshot_mois[:4], snapshot_mois[5:7]
        snapshot_label = f"{MONTH_FR.get(m, m)} {y}"

    try:
        MONTH_ABBR = {
            '01':'Jan','02':'Fév','03':'Mar','04':'Avr','05':'Mai','06':'Jun',
            '07':'Jul','08':'Aoû','09':'Sep','10':'Oct','11':'Nov','12':'Déc'
        }
        if USE_POSTGRES:
            month_rows = fetchall("""
                SELECT TO_CHAR(DATE_TRUNC('month', TO_DATE(date_tournoi, 'DD/MM/YYYY')), 'Mon YYYY') AS mois,
                       DATE_TRUNC('month', TO_DATE(date_tournoi, 'DD/MM/YYYY')) AS mois_sort,
                       COUNT(DISTINCT id_tournoi) AS nb
                FROM participations
                WHERE date_tournoi IS NOT NULL
                  AND date_tournoi ~ '^[0-9]{2}/[0-9]{2}/[0-9]{4}$'
                GROUP BY DATE_TRUNC('month', TO_DATE(date_tournoi, 'DD/MM/YYYY'))
                ORDER BY DATE_TRUNC('month', TO_DATE(date_tournoi, 'DD/MM/YYYY')) DESC
                LIMIT 12
            """)
            last_12 = [(r["mois"], r["nb"]) for r in reversed(month_rows)]
        else:
            # Aggregate entirely in SQLite — avoids loading 650k rows into Python
            month_rows = fetchall("""
                SELECT
                  SUBSTR(date_tournoi,4,2) || '/' || SUBSTR(date_tournoi,7,4) AS mois,
                  COUNT(DISTINCT id_tournoi) AS nb
                FROM participations
                WHERE date_tournoi IS NOT NULL AND LENGTH(date_tournoi) = 10
                GROUP BY mois
                ORDER BY SUBSTR(date_tournoi,7,4) || SUBSTR(date_tournoi,4,2) DESC
                LIMIT 12
            """)
            last_12 = [
                (MONTH_ABBR.get(r["mois"][:2], r["mois"][:2]) + ' ' + r["mois"][3:], r["nb"])
                for r in reversed(month_rows)
            ]
    except Exception as e:
        print(f"   [stats] monthly FAILED: {e}", flush=True)
        last_12 = []
    _step("monthly", _t0); _t0 = _t.time()

    villes = fetchall("""
        SELECT UPPER(TRIM(ville)) AS ville, COUNT(*) AS nb
        FROM joueurs WHERE ville IS NOT NULL AND ville != ''
        GROUP BY UPPER(TRIM(ville))
        ORDER BY nb DESC LIMIT 10
    """)
    _step("villes", _t0); _t0 = _t.time()

    try:
        if USE_POSTGRES:
            tdist_rows = fetchall("""
                SELECT
                  COUNT(*) FILTER (WHERE nb_parts <= 16)               AS b1,
                  COUNT(*) FILTER (WHERE nb_parts BETWEEN 17 AND 32)   AS b2,
                  COUNT(*) FILTER (WHERE nb_parts BETWEEN 33 AND 64)   AS b3,
                  COUNT(*) FILTER (WHERE nb_parts BETWEEN 65 AND 128)  AS b4,
                  COUNT(*) FILTER (WHERE nb_parts BETWEEN 129 AND 256) AS b5,
                  COUNT(*) FILTER (WHERE nb_parts > 256)               AS b6
                FROM (
                  SELECT id_tournoi, COUNT(*) AS nb_parts
                  FROM participations GROUP BY id_tournoi
                ) t
            """)
            r0 = tdist_rows[0] if tdist_rows else {}
            tdist = [int(r0.get("b1") or 0), int(r0.get("b2") or 0), int(r0.get("b3") or 0),
                     int(r0.get("b4") or 0), int(r0.get("b5") or 0), int(r0.get("b6") or 0)]
        else:
            # Aggregate in SQLite — avoids loading all tournament sizes into Python
            tdist_row = fetchall("""
                SELECT
                  SUM(CASE WHEN nb_parts/2 <= 8   THEN 1 ELSE 0 END) AS b1,
                  SUM(CASE WHEN nb_parts/2 BETWEEN 9  AND 16  THEN 1 ELSE 0 END) AS b2,
                  SUM(CASE WHEN nb_parts/2 BETWEEN 17 AND 32  THEN 1 ELSE 0 END) AS b3,
                  SUM(CASE WHEN nb_parts/2 BETWEEN 33 AND 64  THEN 1 ELSE 0 END) AS b4,
                  SUM(CASE WHEN nb_parts/2 BETWEEN 65 AND 128 THEN 1 ELSE 0 END) AS b5,
                  SUM(CASE WHEN nb_parts/2 > 128              THEN 1 ELSE 0 END) AS b6
                FROM (SELECT id_tournoi, COUNT(*) AS nb_parts FROM participations GROUP BY id_tournoi)
            """)
            r0 = tdist_row[0] if tdist_row else {}
            tdist = [int(r0.get(f"b{i}") or 0) for i in range(1, 7)]
    except Exception as e:
        print(f"   [stats] tdist FAILED: {e}", flush=True)
        tdist = [0, 0, 0, 0, 0, 0]
    _step("tdist", _t0); _t0 = _t.time()

    resp = jsonify({
        "nb_joueurs":         int(counts.get("nb_joueurs") or 0),
        "nb_joueurs_classes": int(counts.get("nb_joueurs_classes") or 0),
        "nb_joueurs_h":       int(counts.get("nb_joueurs_h") or 0),
        "nb_joueurs_f":       int(counts.get("nb_joueurs_f") or 0),
        "nb_tournois":        int(counts.get("nb_tournois") or 0),
        "nb_participations":  int(counts.get("nb_participations") or 0),
        "snapshot_mois":      snapshot_mois,    # "2025-05"
        "snapshot_label":     snapshot_label,   # "Mai 2025"
        "ranking_dist": [
            ranking.get("top100", 0), ranking.get("c100_1k", 0),
            ranking.get("c1k_5k", 0), ranking.get("c5k_20k", 0),
            ranking.get("c20k_40k", 0), ranking.get("c40k_80k", 0),
            ranking.get("c80kplus", 0),
        ],
        "ranking_dist_h": [
            ranking.get("top100_h", 0), ranking.get("c100_1k_h", 0),
            ranking.get("c1k_5k_h", 0), ranking.get("c5k_20k_h", 0),
            ranking.get("c20k_40k_h", 0), ranking.get("c40k_80k_h", 0),
            ranking.get("c80kplus_h", 0),
        ],
        "ranking_dist_f": [
            ranking.get("top100_f", 0), ranking.get("c100_1k_f", 0),
            ranking.get("c1k_5k_f", 0), ranking.get("c5k_20k_f", 0),
            ranking.get("c20k_40k_f", 0), ranking.get("c40k_80k_f", 0),
            ranking.get("c80kplus_f", 0),
        ],
        "pyramid": {
            "labels": ["<18", "18-25", "26-35", "36-45", "46-55", "56-65", "65+"],
            "hommes": pyramid["H"],
            "femmes": pyramid["F"],
        },
        "monthly": {
            "labels": [m[0] for m in last_12],
            "data":   [m[1] for m in last_12],
        },
        "top_villes": [{"ville": r["ville"], "nb": r["nb"]} for r in villes],
        "tournament_dist": tdist,
    })
    try: _store_in_mem("stats", resp.get_data())
    except Exception: pass
    return resp


@app.get("/api/leaderboard")
def route_leaderboard():
    from db import fetchall, fetchone
    current_year = datetime.date.today().year

    sexe          = request.args.get("sexe", "H").upper()
    club          = request.args.get("club", "").strip()
    club_variants = request.args.getlist("club_variant")  # liste de noms exacts
    q             = request.args.get("q", "").strip()
    age           = request.args.get("age", "")
    offset        = max(0, int(request.args.get("offset", 0)))
    limit         = min(int(request.args.get("limit", 50)), 100)

    conditions = ["j.classement IS NOT NULL"]
    params = []

    if sexe in ("H", "F"):
        conditions.append("j.sexe = ?")
        params.append(sexe)

    ville  = request.args.get("ville", "").strip()

    if club_variants:
        # Filtre multi-variantes : IN liste exacte
        placeholders = ",".join(["?" ] * len(club_variants))
        conditions.append(f"j.club_nom IN ({placeholders})")
        params.extend(club_variants)
    elif club:
        like_op = "ILIKE" if USE_POSTGRES else "LIKE"
        conditions.append(f"j.club_nom {like_op} ?")
        params.append("%" + club + "%")

    if ville:
        conditions.append("j.ville LIKE ?")
        params.append("%" + ville + "%")

    if q:
        pattern = "%" + q + "%"
        conditions.append(
            "(j.nom LIKE ? OR j.prenom LIKE ? OR (j.nom || ' ' || j.prenom) LIKE ? OR (j.prenom || ' ' || j.nom) LIKE ?)"
        )
        params.extend([pattern, pattern, pattern, pattern])

    if age == "u18":
        conditions.append("j.naissance IS NOT NULL AND (? - CAST(j.naissance AS INT)) < 18")
        params.append(current_year)
    elif age == "18-35":
        conditions.append("j.naissance IS NOT NULL AND (? - CAST(j.naissance AS INT)) BETWEEN 18 AND 35")
        params.append(current_year)
    elif age == "35-50":
        conditions.append("j.naissance IS NOT NULL AND (? - CAST(j.naissance AS INT)) BETWEEN 35 AND 50")
        params.append(current_year)
    elif age == "50+":
        conditions.append("j.naissance IS NOT NULL AND (? - CAST(j.naissance AS INT)) >= 50")
        params.append(current_year)

    ligue  = request.args.get("ligue", "").strip()
    comite = request.args.get("comite", "").strip()
    dept   = request.args.get("dept", "").strip()
    if ligue:
        conditions.append("j.ligue = ?")
        params.append(ligue)
    if comite:
        conditions.append("j.comite = ?")
        params.append(comite)
    if dept:
        conditions.append("j.dept_num = ?")
        params.append(dept)

    where = " AND ".join(conditions)
    total_row = fetchone("SELECT COUNT(*) AS n FROM joueurs j WHERE " + where, tuple(params))
    total = total_row["n"] if total_row else 0

    rows = fetchall(
        "SELECT j.id_fft, j.nom, j.prenom, j.classement, j.meilleur_classement,"
        " j.variation_classement, j.classement_date,"
        " j.club_nom, j.ville, j.sexe, j.naissance,"
        " j.ligue, j.comite, j.dept_num, j.points,"
        " (SELECT COUNT(*) FROM participations p WHERE p.id_joueur = j.id_fft) AS nb_tournois"
        " FROM joueurs j WHERE " + where +
        " ORDER BY j.classement ASC LIMIT ? OFFSET ?",
        tuple(params) + (limit, offset),
    )

    def fmt(r):
        prenom = (r.get("prenom") or "").strip()
        nom    = r.get("nom") or ""
        naissance_annee = None
        try:
            if r.get("naissance") and str(r["naissance"]).isdigit():
                naissance_annee = int(r["naissance"])
        except Exception:
            pass
        return {
            "id":                    r["id_fft"],
            "nom":                   nom,
            "prenom":                prenom,
            "nom_complet":           (prenom + " " + nom).strip(),
            "classement":            r["classement"],
            "meilleur_classement":   r["meilleur_classement"],
            "variation_classement":  r.get("variation_classement"),  # delta mensuel FFT
            "classement_date":       r.get("classement_date"),       # mois du snapshot
            "club":                  r["club_nom"] or "",
            "ville":                 r["ville"] or "",
            "sexe":                  r["sexe"] or "",
            "naissance_annee":       naissance_annee,
            "ligue":                 r.get("ligue") or "",
            "comite":                r.get("comite") or "",
            "dept_num":              r.get("dept_num") or "",
            "points":                r.get("points"),
            "nb_tournois":           r["nb_tournois"] or 0,
        }

    return jsonify({"players": [fmt(r) for r in rows], "total": total, "offset": offset})


@app.get("/api/movers")
def route_movers():
    from db import fetchall
    sexe = request.args.get("sexe", "").upper()
    n    = min(int(request.args.get("n", 8)), 20)
    sf   = "AND sexe = ?" if sexe in ("H", "F") else ""
    bp   = (sexe,) if sexe in ("H", "F") else ()

    hausse = fetchall(
        "SELECT id_fft, nom, prenom, classement, meilleur_classement, club_nom, ville, sexe"
        " FROM joueurs WHERE classement IS NOT NULL AND meilleur_classement IS NOT NULL"
        " AND classement <= 5000 AND classement = meilleur_classement " + sf +
        " ORDER BY classement ASC LIMIT ?",
        bp + (n,),
    )

    baisse = fetchall(
        "SELECT id_fft, nom, prenom, classement, meilleur_classement, club_nom, ville, sexe,"
        " (classement - meilleur_classement) AS chute"
        " FROM joueurs WHERE classement IS NOT NULL AND meilleur_classement IS NOT NULL"
        " AND classement > meilleur_classement AND nom IS NOT NULL AND nom != '' " + sf +
        " ORDER BY chute DESC LIMIT ?",
        bp + (n,),
    )

    def fmt(r, delta=None):
        prenom = (r.get("prenom") or "").strip()
        return {
            "id":                  r["id_fft"],
            "nom_complet":         (prenom + " " + (r.get("nom") or "")).strip(),
            "classement":          r["classement"],
            "meilleur_classement": r["meilleur_classement"],
            "club":                r["club_nom"] or "",
            "ville":               r["ville"] or "",
            "delta":               delta,
        }

    return jsonify({
        "hausse": [fmt(r) for r in hausse],
        "baisse": [fmt(r, r["chute"]) for r in baisse],
    })


_CLUB_PREFIXES = re.compile(
    r'^(?:T\.?C\.?\s+|A\.?S\.?\s+|U\.?S\.?\s+|C\.?A\.?\s+|S\.?C\.?\s+|'
    r'CLUB\s+|CERCLE\s+|TENNIS\s+CLUB\s+)',
    re.IGNORECASE
)

def _normalize_club(name: str) -> str:
    """Supprime les préfixes courants pour permettre la fusion des variantes."""
    return _CLUB_PREFIXES.sub('', name).upper().strip()


@app.get("/api/clubs")
def route_clubs():
    from db import fetchall
    top = min(int(request.args.get("top", 100)), 500)
    q   = request.args.get("q", "").strip()
    like_op = "ILIKE" if USE_POSTGRES else "LIKE"

    # Cache pour la liste par défaut (pas de filtre `q`)
    _cache_key = f"clubs_{top}" if not q else None
    if _cache_key:
        cached = _try_precomputed(_cache_key)
        if cached is not None:
            return cached

    if q:
        rows = fetchall(f"""
            SELECT club_nom,
              (SELECT j2.ville FROM joueurs j2
               WHERE j2.club_nom = j.club_nom AND j2.ville IS NOT NULL AND j2.ville != ''
               GROUP BY j2.ville ORDER BY COUNT(*) DESC LIMIT 1) AS ville,
              COUNT(*) AS nb_joueurs
            FROM joueurs j
            WHERE club_nom IS NOT NULL AND club_nom != ''
              AND club_nom {like_op} ?
            GROUP BY club_nom
            ORDER BY nb_joueurs DESC LIMIT ?
        """, ("%" + q + "%", top * 4))  # fetch more to allow merging
    else:
        rows = fetchall("""
            SELECT club_nom,
              (SELECT j2.ville FROM joueurs j2
               WHERE j2.club_nom = j.club_nom AND j2.ville IS NOT NULL AND j2.ville != ''
               GROUP BY j2.ville ORDER BY COUNT(*) DESC LIMIT 1) AS ville,
              COUNT(*) AS nb_joueurs
            FROM joueurs j
            WHERE club_nom IS NOT NULL AND club_nom != ''
            GROUP BY club_nom
            ORDER BY nb_joueurs DESC LIMIT ?
        """, (top * 4,))

    # ── Fusionner les variantes par nom normalisé ────────────────────────────
    # Ex: "TC LES LILAS" et "LES LILAS" → même clé "LES LILAS", 1 entrée fusionnée
    merged: dict[str, dict] = {}
    for r in rows:
        key = _normalize_club(r["club_nom"])
        if not key:
            continue
        if key not in merged:
            merged[key] = {"nom": r["club_nom"], "ville": r["ville"],
                           "nb": r["nb_joueurs"], "variants": [r["club_nom"]]}
        else:
            merged[key]["nb"] += r["nb_joueurs"]
            merged[key]["variants"].append(r["club_nom"])
            # Garder le nom de variante avec le plus de licenciés
            if r["nb_joueurs"] > merged[key]["nb"] - r["nb_joueurs"]:
                merged[key]["nom"] = r["club_nom"]
                merged[key]["ville"] = r["ville"]

    result = sorted(merged.values(), key=lambda x: -x["nb"])[:top]
    resp = jsonify([
        {"nom": e["nom"], "ville": e["ville"], "nb": e["nb"],
         "variants": e["variants"]}
        for e in result
    ])
    if _cache_key:
        try: _store_in_mem(_cache_key, resp.get_data())
        except Exception: pass
    return resp


@app.get("/api/tournaments")
def route_tournaments():
    from db import fetchall, fetchone
    q     = request.args.get("q", "").strip()
    limit = min(int(request.args.get("limit", 20)), 50)

    # 1. Cache JSON pré-calculé (rempli par precompute.py) — ~5ms
    _cache_key = f"tournaments_{limit}" if not q else None
    if _cache_key:
        cached = _try_precomputed(_cache_key)
        if cached is not None:
            return cached

    _lk = "ILIKE" if USE_POSTGRES else "LIKE"
    # Filtres d'exclusion des championnats/épreuves — adaptés aux colonnes de tournois_summary
    _excl = (
        f"nom NOT {_lk} '%CHAMPIONNAT%' AND nom NOT {_lk} '%EPREUVE%'"
        f" AND (categorie IS NULL OR (categorie NOT {_lk} 'CHAMP%'"
        f"   AND categorie NOT {_lk} 'EPRE%'))"
    )

    # 2. Lecture depuis tournois_summary si peuplée (matérialisée par precompute.py)
    #    → ~3 000 lignes indexées, pas de JOIN sur participations → <10ms
    _summary_ok = False
    try:
        _summary_ok = bool(fetchone("SELECT 1 FROM tournois_summary LIMIT 1"))
    except Exception:
        pass

    if _summary_ok:
        if q:
            rows = fetchall(f"""
                SELECT id_tournoi, nom, categorie,
                       date_min AS date_tournoi,
                       nb_joueurs
                FROM tournois_summary
                WHERE nom {_lk} ?
                  AND {_excl}
                ORDER BY date_sort DESC
                LIMIT ?
            """, ("%" + q + "%", limit))
        else:
            rows = fetchall(f"""
                SELECT id_tournoi, nom, categorie,
                       date_min AS date_tournoi,
                       nb_joueurs
                FROM tournois_summary
                WHERE {_excl}
                ORDER BY date_sort DESC
                LIMIT ?
            """, (limit,))
    else:
        # Fallback : requête lourde originale (avant première exécution de precompute)
        _date_sort = (
            "TO_DATE(MIN(p.date_tournoi), 'DD/MM/YYYY') DESC" if USE_POSTGRES else
            "SUBSTR(MIN(p.date_tournoi),7,4)||SUBSTR(MIN(p.date_tournoi),4,2)"
            "||SUBSTR(MIN(p.date_tournoi),1,2) DESC"
        )
        _excl_t = (
            f"t.nom NOT {_lk} '%CHAMPIONNAT%' AND t.nom NOT {_lk} '%EPREUVE%'"
            f" AND (t.categorie IS NULL OR (t.categorie NOT {_lk} 'CHAMP%'"
            f"   AND t.categorie NOT {_lk} 'EPRE%'))"
        )
        if q:
            rows = fetchall(f"""
                SELECT t.id_tournoi, t.nom, t.categorie,
                       MIN(p.date_tournoi) AS date_tournoi,
                       COUNT(DISTINCT p.id_joueur) AS nb_joueurs
                FROM tournois t
                JOIN participations p ON p.id_tournoi = t.id_tournoi
                WHERE t.nom {_lk} ? AND {_excl_t}
                GROUP BY t.id_tournoi, t.nom, t.categorie
                ORDER BY {_date_sort} LIMIT ?
            """, ("%" + q + "%", limit))
        else:
            rows = fetchall(f"""
                SELECT t.id_tournoi, t.nom, t.categorie,
                       MIN(p.date_tournoi) AS date_tournoi,
                       COUNT(DISTINCT p.id_joueur) AS nb_joueurs
                FROM tournois t
                JOIN participations p ON p.id_tournoi = t.id_tournoi
                WHERE {_excl_t}
                GROUP BY t.id_tournoi, t.nom, t.categorie
                ORDER BY {_date_sort} LIMIT ?
            """, (limit,))

    resp = jsonify([{
        "id": r["id_tournoi"], "nom": r["nom"] or "", "categorie": r["categorie"] or "",
        "date": r["date_tournoi"] or "", "nb_joueurs": r["nb_joueurs"] or 0,
    } for r in rows])
    if _cache_key:
        try: _store_in_mem(_cache_key, resp.get_data())
        except Exception: pass
    return resp


@app.get("/api/tournament/<tid>")
def route_tournament(tid: str):
    from db import fetchall, fetchone
    info = fetchone("""
        SELECT t.id_tournoi, t.nom, t.categorie,
               MIN(p.date_tournoi) AS date_tournoi,
               COUNT(DISTINCT p.id_joueur) AS nb_joueurs,
               COUNT(DISTINCT p.partenaire_id) AS nb_paires_raw
        FROM tournois t
        JOIN participations p ON p.id_tournoi = t.id_tournoi
        WHERE t.id_tournoi = ?
        GROUP BY t.id_tournoi, t.nom, t.categorie
    """, (tid,))
    if not info:
        return jsonify({"error": "Tournoi introuvable"}), 404

    # All results (pairs deduplicated by position)
    results = fetchall("""
        SELECT p.id_joueur, p.partenaire_id, p.partenaire_nom,
               p.position, p.points,
               j.nom, j.prenom, j.classement
        FROM participations p
        JOIN joueurs j ON j.id_fft = p.id_joueur
        WHERE p.id_tournoi = ?
        ORDER BY CAST(p.position AS INTEGER) ASC, j.classement ASC
    """, (tid,))

    # Deduplicate pairs (same pair appears twice: once per player)
    # Strategy:
    #  1. If both partenaire_id are known → frozenset(id1,id2) dedup
    #  2. Fallback: if joueur1 already seen in any pair → skip
    #  3. Fallback: if partenaire_nom matches an already-seen player's nom_complet → skip
    seen_ids    = set()   # frozenset of two IDs already kept
    seen_players = set()  # individual player IDs already in any kept pair
    seen_noms   = set()   # normalised full names of players already kept
    pairs = []
    for r in results:
        pid   = r["id_joueur"]
        ppid  = r["partenaire_id"] or ""
        pnom  = (r["partenaire_nom"] or "").strip().upper()
        joueur1_nom = f"{(r.get('prenom') or '').strip()} {r.get('nom') or ''}".strip().upper()
        key   = frozenset([pid, ppid]) if ppid else None

        # 1. Skip if exact pair (IDs) already recorded
        if key and key in seen_ids:
            continue
        # 2. Skip if joueur1 already appears in a previously kept pair
        if pid in seen_players:
            continue
        # 3. Skip if joueur1's name matches a partenaire_nom already recorded
        if joueur1_nom and joueur1_nom in seen_noms:
            continue

        seen_players.add(pid)
        seen_noms.add(joueur1_nom)
        if ppid:
            seen_players.add(ppid)
            seen_ids.add(key)
        if pnom:
            seen_noms.add(pnom)

        pairs.append({
            "position":     r["position"],
            "points":       r["points"],
            "joueur1_id":   r["id_joueur"],
            "joueur1_nom":  f"{(r.get('prenom') or '').strip()} {r.get('nom') or ''}".strip(),
            "joueur1_cl":   r["classement"],
            "joueur2_id":   r["partenaire_id"] or "",
            "joueur2_nom":  r["partenaire_nom"] or "",
        })

    return jsonify({
        "id":          info["id_tournoi"],
        "nom":         info["nom"] or "",
        "categorie":   info["categorie"] or "",
        "date":        info["date_tournoi"] or "",
        "nb_joueurs":  info["nb_joueurs"] or 0,
        "nb_paires":   len(pairs),
        "pairs":       pairs[:64],
    })


@app.get("/api/club")
def route_club():
    from db import fetchall, fetchone
    nom = request.args.get("nom", "").strip()
    if not nom:
        return jsonify({"error": "nom requis"}), 400

    # ── Trouver toutes les variantes du club (même nom normalisé) ──────────────
    # Utilise LIKE sur le nom sans préfixe (index idx_joueurs_club → ~50ms)
    from_key = _normalize_club(nom)
    like_op = "ILIKE" if USE_POSTGRES else "LIKE"
    cand_rows = fetchall(
        f"SELECT DISTINCT club_nom FROM joueurs WHERE UPPER(club_nom) {like_op} ?",
        (f"%{from_key}%",)
    )
    variants = [r["club_nom"] for r in cand_rows if _normalize_club(r["club_nom"]) == from_key]
    if nom not in variants:
        variants.append(nom)
    if not variants:
        variants = [nom]
    ph = ','.join('?' * len(variants))   # "?,?,?"
    vt = tuple(variants)                  # params pour IN

    # ── Stats globales du club (toutes variantes confondues) ────────────────
    stats = fetchone(f"""
        SELECT
          COUNT(*) AS nb_joueurs,
          SUM(CASE WHEN sexe = 'H' THEN 1 ELSE 0 END) AS nb_h,
          SUM(CASE WHEN sexe = 'F' THEN 1 ELSE 0 END) AS nb_f,
          MIN(CASE WHEN sexe = 'H' AND classement IS NOT NULL THEN classement END) AS best_h,
          MIN(CASE WHEN sexe = 'F' AND classement IS NOT NULL THEN classement END) AS best_f,
          ROUND(AVG(CASE WHEN sexe='H' THEN classement END)) AS avg_rank_h,
          ROUND(AVG(CASE WHEN sexe='F' THEN classement END)) AS avg_rank_f,
          SUM(CASE WHEN sexe='H' AND classement IS NOT NULL AND classement <= 100   THEN 1 ELSE 0 END) AS top100_h,
          SUM(CASE WHEN sexe='F' AND classement IS NOT NULL AND classement <= 100   THEN 1 ELSE 0 END) AS top100_f,
          SUM(CASE WHEN sexe='H' AND classement IS NOT NULL AND classement <= 1000  THEN 1 ELSE 0 END) AS top1000_h,
          SUM(CASE WHEN sexe='F' AND classement IS NOT NULL AND classement <= 1000  THEN 1 ELSE 0 END) AS top1000_f,
          SUM(CASE WHEN sexe='H' AND classement IS NOT NULL AND classement <= 10000 THEN 1 ELSE 0 END) AS top10000_h,
          SUM(CASE WHEN sexe='F' AND classement IS NOT NULL AND classement <= 10000 THEN 1 ELSE 0 END) AS top10000_f
        FROM joueurs
        WHERE club_nom IN ({ph})
    """, vt) or {}

    ville_row = fetchone(f"""
        SELECT ville, COUNT(*) AS cnt
        FROM joueurs
        WHERE club_nom IN ({ph}) AND ville IS NOT NULL AND ville != ''
        GROUP BY ville
        ORDER BY cnt DESC
        LIMIT 1
    """, vt) or {}

    # ── Nb tournois organisés dans les 12 derniers mois ─────────────────────
    import datetime as _dt
    _today = _dt.date.today()
    _cm = _today.month
    _cy = _today.year
    if _cm == 12:
        _start_12m = f"01/01/{_cy}"
    else:
        _start_12m = f"01/{(_cm+1):02d}/{_cy-1}"
    if USE_POSTGRES:
        _date_filter_12m = f"p.date_tournoi >= TO_DATE('{_start_12m}', 'DD/MM/YYYY')"
    else:
        _yy, _mm, _dd = _start_12m[6:10], _start_12m[3:5], _start_12m[0:2]
        _start_lex = f"{_yy}/{_mm}/{_dd}"
        _date_filter_12m = (
            f"SUBSTR(p.date_tournoi,7,4)||'/'||SUBSTR(p.date_tournoi,4,2)||'/'||SUBSTR(p.date_tournoi,1,2)"
            f" >= '{_start_lex}'"
        )
    nb_tournois_row = fetchone(f"""
        SELECT COUNT(DISTINCT p.id_tournoi) AS nb_t
        FROM participations p
        JOIN joueurs j2 ON j2.id_fft = p.id_joueur
        WHERE j2.club_nom IN ({ph})
          AND {_date_filter_12m}
    """, vt) or {}

    # Top joueurs hommes (tous les membres classés)
    top_h = fetchall(f"""
        SELECT id_fft, nom, prenom, classement, meilleur_classement,
               (SELECT COUNT(*) FROM participations p WHERE p.id_joueur = j.id_fft) AS nb_tournois
        FROM joueurs j
        WHERE club_nom IN ({ph}) AND sexe = 'H' AND classement IS NOT NULL
        ORDER BY classement ASC LIMIT 100
    """, vt)

    # Top joueurs femmes (toutes les membres classées)
    top_f = fetchall(f"""
        SELECT id_fft, nom, prenom, classement, meilleur_classement,
               (SELECT COUNT(*) FROM participations p WHERE p.id_joueur = j.id_fft) AS nb_tournois
        FROM joueurs j
        WHERE club_nom IN ({ph}) AND sexe = 'F' AND classement IS NOT NULL
        ORDER BY classement ASC LIMIT 100
    """, vt)

    # Club ranking: count clubs with a better (lower) best player rank
    rang_h_row = fetchone(f"""
        SELECT COUNT(*) AS rang
        FROM (
            SELECT club_nom, MIN(classement) AS best
            FROM joueurs
            WHERE sexe = 'H' AND classement IS NOT NULL AND club_nom IS NOT NULL AND club_nom != ''
            GROUP BY club_nom
        ) sub
        WHERE sub.best < (
            SELECT MIN(classement) FROM joueurs WHERE club_nom IN ({ph}) AND sexe = 'H' AND classement IS NOT NULL
        )
    """, vt) or {}
    rang_f_row = fetchone(f"""
        SELECT COUNT(*) AS rang
        FROM (
            SELECT club_nom, MIN(classement) AS best
            FROM joueurs
            WHERE sexe = 'F' AND classement IS NOT NULL AND club_nom IS NOT NULL AND club_nom != ''
            GROUP BY club_nom
        ) sub
        WHERE sub.best < (
            SELECT MIN(classement) FROM joueurs WHERE club_nom IN ({ph}) AND sexe = 'F' AND classement IS NOT NULL
        )
    """, vt) or {}

    # +1 to get 1-based rank; None if no ranked player in that sex
    rang_h = (int(rang_h_row.get("rang") or 0) + 1) if stats.get("best_h") else None
    rang_f = (int(rang_f_row.get("rang") or 0) + 1) if stats.get("best_f") else None

    # ── Rang par tranche de classement — SEPARE H et F ─────────────────────
    # Les classements H et F sont totalement independants en padel FFT :
    # un #100 H et un #100 F n'ont aucune relation (pools differents).
    # On partitionne donc aussi par sexe dans le RANK() OVER.
    tranche_rows = fetchall(f"""
        SELECT sexe, tranche, ordre, nb_club, rang FROM (
            SELECT
              sexe,
              CASE
                WHEN classement BETWEEN 5001  AND 10000 THEN '5k-10k'
                WHEN classement BETWEEN 10001 AND 20000 THEN '10k-20k'
                WHEN classement BETWEEN 20001 AND 50000 THEN '20k-50k'
                WHEN classement > 50000                 THEN '50k+'
              END AS tranche,
              CASE
                WHEN classement BETWEEN 5001  AND 10000 THEN 1
                WHEN classement BETWEEN 10001 AND 20000 THEN 2
                WHEN classement BETWEEN 20001 AND 50000 THEN 3
                WHEN classement > 50000                 THEN 4
              END AS ordre,
              club_nom,
              COUNT(*) AS nb_club,
              RANK() OVER (
                PARTITION BY
                  sexe,
                  CASE
                    WHEN classement BETWEEN 5001  AND 10000 THEN 1
                    WHEN classement BETWEEN 10001 AND 20000 THEN 2
                    WHEN classement BETWEEN 20001 AND 50000 THEN 3
                    WHEN classement > 50000                 THEN 4
                  END
                ORDER BY COUNT(*) DESC
              ) AS rang
            FROM joueurs
            WHERE classement IS NOT NULL
              AND classement > 5000
              AND club_nom IS NOT NULL AND club_nom != ''
              AND sexe IN ('H', 'F')
            GROUP BY sexe, tranche, ordre, club_nom
        ) ranked
        WHERE club_nom IN ({ph})
          AND tranche IS NOT NULL
        ORDER BY sexe DESC, ordre
    """, vt)

    tranches_h = [
        {"tranche": r["tranche"], "nb": int(r["nb_club"] or 0), "rang": int(r["rang"] or 0)}
        for r in tranche_rows if r["sexe"] == 'H'
    ]
    tranches_f = [
        {"tranche": r["tranche"], "nb": int(r["nb_club"] or 0), "rang": int(r["rang"] or 0)}
        for r in tranche_rows if r["sexe"] == 'F'
    ]

    def fmt(r):
        return {
            "id": r["id_fft"], "nom": r["nom"] or "", "prenom": r["prenom"] or "",
            "nom_complet": f"{(r.get('prenom') or '').strip()} {r.get('nom') or ''}".strip(),
            "classement": r["classement"], "meilleur_classement": r["meilleur_classement"],
            "nb_tournois": r["nb_tournois"] or 0,
        }

    return jsonify({
        "nom":        nom,
        "ville":      ville_row.get("ville") or "",
        "nb_joueurs": int(stats.get("nb_joueurs") or 0),
        "nb_h":       int(stats.get("nb_h") or 0),
        "nb_f":       int(stats.get("nb_f") or 0),
        "best_h":     stats.get("best_h"),
        "best_f":     stats.get("best_f"),
        "avg_rank_h": int(stats.get("avg_rank_h") or 0) if stats.get("avg_rank_h") else None,
        "avg_rank_f": int(stats.get("avg_rank_f") or 0) if stats.get("avg_rank_f") else None,
        "top100_h":        int(stats.get("top100_h") or 0),
        "top100_f":        int(stats.get("top100_f") or 0),
        "top1000_h":       int(stats.get("top1000_h") or 0),
        "top1000_f":       int(stats.get("top1000_f") or 0),
        "top10000_h":      int(stats.get("top10000_h") or 0),
        "top10000_f":      int(stats.get("top10000_f") or 0),
        "nb_tournois_12m": int(nb_tournois_row.get("nb_t") or 0),
        "rang_h":     rang_h,
        "rang_f":     rang_f,
        "tranches_h": tranches_h,
        "tranches_f": tranches_f,
        "top_h":      [fmt(r) for r in top_h],
        "top_f":      [fmt(r) for r in top_f],
    })


@app.get("/api/stats/categories")
def route_stats_categories():
    """
    Retourne pour chaque catégorie de tournoi :
      - nb_tournois, avg/min/max joueurs par tournoi
      - top 10 villes (inférées depuis le nom du tournoi ou la ville du club organisateur)
    Les noms de tournoi contiennent souvent "TC <NOM_CLUB>" → on joint avec joueurs.club_nom
    pour récupérer la ville la plus fréquente associée à ce club.
    """
    from db import fetchall, fetchone

    cached = _try_precomputed("stats_categories")
    if cached is not None:
        return cached

    # Opérateur LIKE adapté au moteur (ILIKE = insensible à la casse en PG)
    _lk = "ILIKE" if USE_POSTGRES else "LIKE"
    # Filtres d'exclusion — utilisés sur les deux requêtes ci-dessous
    _excl_ts = (
        f"nom NOT {_lk} '%CHAMPIONNAT%' AND nom NOT {_lk} '%EPREUVE%' "
        f"AND categorie NOT {_lk} '%CHAMP%' AND categorie NOT {_lk} '%EPRE%'"
    )

    # ── Vérifier si tournois_summary est peuplée ─────────────────────────────
    _summary_ok = False
    try:
        _summary_ok = bool(fetchone("SELECT 1 FROM tournois_summary LIMIT 1"))
    except Exception:
        pass

    # Variante de _excl_ts préfixée par "ts." — utilisée dans les JOINs multi-tables
    _excl_ts_join = (
        f"ts.nom NOT {_lk} '%CHAMPIONNAT%' AND ts.nom NOT {_lk} '%EPREUVE%' "
        f"AND ts.categorie NOT {_lk} '%CHAMP%' AND ts.categorie NOT {_lk} '%EPRE%'"
    )

    if _summary_ok:
        # ── Chemin rapide : lecture depuis tournois_summary (~3k lignes) ──────
        # Évite le gros JOIN + subquery GROUP BY sur 800k participations.
        cat_stats = fetchall(f"""
            SELECT
              categorie,
              COUNT(*)               AS nb_tournois,
              ROUND(AVG(nb_joueurs)) AS avg_joueurs,
              MIN(nb_joueurs)        AS min_joueurs,
              MAX(nb_joueurs)        AS max_joueurs
            FROM tournois_summary
            WHERE categorie IS NOT NULL
              AND {_excl_ts}
            GROUP BY categorie
            ORDER BY nb_tournois DESC
        """)

        # Top villes : join participations×joueurs ancré sur tournois_summary
        # (toujours une jointure lourde, mais couverte par le JSON cache ci-dessus)
        city_rows = fetchall(f"""
            SELECT
              ts.categorie,
              j.ville,
              COUNT(DISTINCT ts.id_tournoi) AS nb
            FROM tournois_summary ts
            JOIN participations p ON p.id_tournoi = ts.id_tournoi
            JOIN joueurs j        ON j.id_fft      = p.id_joueur
            WHERE ts.categorie IS NOT NULL
              AND j.ville IS NOT NULL AND j.ville != ''
              AND {_excl_ts_join}
            GROUP BY ts.categorie, j.ville
            ORDER BY ts.categorie, nb DESC
        """)
    else:
        # ── Fallback : requêtes lourdes originales (avant première exécution de precompute) ──
        _excl = (
            f"t.nom NOT {_lk} '%CHAMPIONNAT%' AND t.nom NOT {_lk} '%EPREUVE%' "
            f"AND t.categorie NOT {_lk} '%CHAMP%' AND t.categorie NOT {_lk} '%EPRE%'"
        )
        cat_stats = fetchall(f"""
            SELECT
              t.categorie,
              COUNT(DISTINCT t.id_tournoi)   AS nb_tournois,
              ROUND(AVG(sub.nb_joueurs))      AS avg_joueurs,
              MIN(sub.nb_joueurs)             AS min_joueurs,
              MAX(sub.nb_joueurs)             AS max_joueurs
            FROM tournois t
            JOIN (
              SELECT id_tournoi, COUNT(DISTINCT id_joueur) AS nb_joueurs
              FROM participations
              GROUP BY id_tournoi
            ) sub ON sub.id_tournoi = t.id_tournoi
            WHERE t.categorie IS NOT NULL
              AND {_excl}
            GROUP BY t.categorie
            ORDER BY nb_tournois DESC
        """)
        city_rows = fetchall(f"""
            SELECT
              t.categorie,
              j.ville,
              COUNT(DISTINCT t.id_tournoi) AS nb
            FROM tournois t
            JOIN participations p ON p.id_tournoi = t.id_tournoi
            JOIN joueurs j        ON j.id_fft      = p.id_joueur
            WHERE t.categorie IS NOT NULL
              AND j.ville IS NOT NULL AND j.ville != ''
              AND {_excl}
            GROUP BY t.categorie, j.ville
            ORDER BY t.categorie, nb DESC
        """)

    # Group city rows per category, keep top 10
    from collections import defaultdict as _dd
    city_by_cat = _dd(list)
    for r in city_rows:
        cat = r["categorie"]
        if len(city_by_cat[cat]) < 10:
            city_by_cat[cat].append({"ville": r["ville"], "nb": r["nb"]})

    result = []
    for r in cat_stats:
        cat = r["categorie"]
        result.append({
            "categorie":    cat,
            "nb_tournois":  r["nb_tournois"],
            "avg_joueurs":  int(r["avg_joueurs"] or 0),
            "min_joueurs":  r["min_joueurs"],
            "max_joueurs":  r["max_joueurs"],
            "top_villes":   city_by_cat.get(cat, []),
        })

    resp = jsonify(result)
    try: _store_in_mem("stats_categories", resp.get_data())
    except Exception: pass
    return resp


@app.get("/api/stats/rank_clubs")
def route_stats_rank_clubs():
    """
    Pour chaque tranche de classement, retourne les top 5 clubs par nombre de joueurs.
    ?sexe=H|F  (optionnel — filtre par sexe)
    """
    from db import fetchall
    sexe = request.args.get("sexe", "").upper()
    sexe_filter = "AND sexe = ?" if sexe in ("H", "F") else ""
    params = (sexe,) if sexe in ("H", "F") else ()

    rows = fetchall(f"""
        SELECT
          CASE
            WHEN classement BETWEEN    1 AND  1000 THEN '1-1k'
            WHEN classement BETWEEN 1001 AND  5000 THEN '1k-5k'
            WHEN classement BETWEEN 5001 AND 10000 THEN '5k-10k'
            WHEN classement BETWEEN 10001 AND 20000 THEN '10k-20k'
            WHEN classement BETWEEN 20001 AND 50000 THEN '20k-50k'
            ELSE '50k+'
          END AS tranche,
          CASE
            WHEN classement BETWEEN    1 AND  1000 THEN 1
            WHEN classement BETWEEN 1001 AND  5000 THEN 2
            WHEN classement BETWEEN 5001 AND 10000 THEN 3
            WHEN classement BETWEEN 10001 AND 20000 THEN 4
            WHEN classement BETWEEN 20001 AND 50000 THEN 5
            ELSE 6
          END AS ordre,
          club_nom,
          COUNT(*) AS nb
        FROM joueurs
        WHERE classement IS NOT NULL
          AND club_nom IS NOT NULL AND club_nom != ''
          {sexe_filter}
        GROUP BY tranche, ordre, club_nom
        ORDER BY ordre ASC, nb DESC
    """, params)

    from collections import defaultdict as _dd
    by_tranche = _dd(list)
    for r in rows:
        t = r["tranche"]
        if len(by_tranche[t]) < 5:
            by_tranche[t].append({"club": r["club_nom"], "nb": r["nb"]})

    TRANCHES = ['1-1k', '1k-5k', '5k-10k', '10k-20k', '20k-50k', '50k+']
    return jsonify([
        {"tranche": t, "clubs": by_tranche.get(t, [])}
        for t in TRANCHES
    ])


@app.get("/api/club_rankings")
def route_club_rankings():
    """
    Classement des clubs selon différents critères.
    ?sort=best_h|best_f|avg_rank|nb_tournois  (défaut: best_h)
    ?top=100  (max 500)
    Retourne la liste des clubs avec leurs métriques principales.
    """
    from db import fetchall
    import datetime

    sort   = request.args.get("sort", "best_h")
    top    = min(int(request.args.get("top", 100)), 500)
    if sort not in ("best_h", "best_f", "avg_rank_h", "avg_rank_f", "nb_tournois"):
        sort = "best_h"

    # Cache — les tris lourds (nb_tournois) peuvent prendre 20-30s sans cache
    _cache_key = f"club_rankings_{sort}_{top}"
    cached = _try_precomputed(_cache_key)
    if cached is not None:
        return cached

    current_year  = datetime.date.today().year
    current_month = datetime.date.today().month
    # Début de la fenêtre 12 mois (format DD/MM/YYYY)
    if current_month == 12:
        start_12m = f"01/01/{current_year}"
    else:
        m = f"{current_month + 1:02d}"
        start_12m = f"01/{m}/{current_year - 1}"

    if USE_POSTGRES:
        date_filter = f"p.date_tournoi >= TO_DATE('{start_12m}', 'DD/MM/YYYY')"
    else:
        # SQLite: convertir DD/MM/YYYY → YYYY/MM/DD pour comparaison lexicographique
        yy, mm, dd = start_12m[6:10], start_12m[3:5], start_12m[0:2]
        start_lex = f"{yy}/{mm}/{dd}"
        date_filter = (
            f"SUBSTR(p.date_tournoi,7,4)||'/'||SUBSTR(p.date_tournoi,4,2)||'/'||SUBSTR(p.date_tournoi,1,2) "
            f">= '{start_lex}'"
        )

    sort_col = {
        "best_h":     "best_h ASC NULLS LAST",
        "best_f":     "best_f ASC NULLS LAST",
        "avg_rank_h": "avg_rank_h ASC NULLS LAST",
        "avg_rank_f": "avg_rank_f ASC NULLS LAST",
        "nb_tournois": "nb_tournois_12m DESC",
    }[sort]

    # ASC NULLS LAST n'est pas supporté en SQLite avant 3.30 — on utilise CASE WHEN
    if not USE_POSTGRES:
        sort_col = {
            "best_h":     "CASE WHEN best_h IS NULL THEN 99999999 ELSE best_h END ASC",
            "best_f":     "CASE WHEN best_f IS NULL THEN 99999999 ELSE best_f END ASC",
            "avg_rank_h": "CASE WHEN avg_rank_h IS NULL THEN 99999999 ELSE avg_rank_h END ASC",
            "avg_rank_f": "CASE WHEN avg_rank_f IS NULL THEN 99999999 ELSE avg_rank_f END ASC",
            "nb_tournois": "nb_tournois_12m DESC",
        }[sort]

    # ── Requête principale (uniquement sur joueurs — rapide) ─────────────────
    # Pour best_h/best_f/avg_rank : on n'a pas besoin de la table participations.
    # Pour nb_tournois : on fait un second JOIN ciblé, uniquement quand demandé.
    if sort == "nb_tournois":
        # JOIN lourd sur participations — acceptable car demandé explicitement
        rows = fetchall(f"""
            WITH club_stats AS (
              SELECT
                j.club_nom,
                MAX(j.ville)                                                                AS ville,
                COUNT(*)                                                                    AS nb_joueurs,
                SUM(CASE WHEN j.sexe='H' THEN 1 ELSE 0 END)                               AS nb_h,
                SUM(CASE WHEN j.sexe='F' THEN 1 ELSE 0 END)                               AS nb_f,
                MIN(CASE WHEN j.sexe='H' AND j.classement IS NOT NULL THEN j.classement END) AS best_h,
                MIN(CASE WHEN j.sexe='F' AND j.classement IS NOT NULL THEN j.classement END) AS best_f,
                ROUND(AVG(CASE WHEN j.sexe='H' THEN j.classement END))                    AS avg_rank_h,
                ROUND(AVG(CASE WHEN j.sexe='F' THEN j.classement END))                    AS avg_rank_f
              FROM joueurs j
              WHERE j.club_nom IS NOT NULL AND j.club_nom != ''
              GROUP BY j.club_nom
              HAVING COUNT(*) >= 3
            ),
            club_tournois AS (
              SELECT j2.club_nom, COUNT(DISTINCT p.id_tournoi) AS nb_t
              FROM participations p
              JOIN joueurs j2 ON j2.id_fft = p.id_joueur
              WHERE j2.club_nom IS NOT NULL AND j2.club_nom != ''
                AND {date_filter}
              GROUP BY j2.club_nom
            )
            SELECT
              cs.club_nom, cs.ville, cs.nb_joueurs, cs.nb_h, cs.nb_f,
              cs.best_h, cs.best_f, cs.avg_rank_h, cs.avg_rank_f,
              COALESCE(ct.nb_t, 0) AS nb_tournois_12m
            FROM club_stats cs
            LEFT JOIN club_tournois ct ON ct.club_nom = cs.club_nom
            ORDER BY nb_tournois_12m DESC
            LIMIT ?
        """, (top,))
    else:
        # Requête rapide — joueurs uniquement, sous-requête pour ORDER BY propre
        rows = fetchall(f"""
            SELECT * FROM (
              SELECT
                j.club_nom,
                MAX(j.ville)                                                                AS ville,
                COUNT(*)                                                                    AS nb_joueurs,
                SUM(CASE WHEN j.sexe='H' THEN 1 ELSE 0 END)                               AS nb_h,
                SUM(CASE WHEN j.sexe='F' THEN 1 ELSE 0 END)                               AS nb_f,
                MIN(CASE WHEN j.sexe='H' AND j.classement IS NOT NULL THEN j.classement END) AS best_h,
                MIN(CASE WHEN j.sexe='F' AND j.classement IS NOT NULL THEN j.classement END) AS best_f,
                ROUND(AVG(CASE WHEN j.sexe='H' THEN j.classement END))                    AS avg_rank_h,
                ROUND(AVG(CASE WHEN j.sexe='F' THEN j.classement END))                    AS avg_rank_f,
                0                                                                          AS nb_tournois_12m
              FROM joueurs j
              WHERE j.club_nom IS NOT NULL AND j.club_nom != ''
              GROUP BY j.club_nom
              HAVING COUNT(*) >= 3
            ) sub
            ORDER BY {sort_col}
            LIMIT ?
        """, (top,))

    return jsonify([{
        "nom":            r["club_nom"],
        "ville":          r["ville"] or "",
        "nb_joueurs":     int(r["nb_joueurs"] or 0),
        "nb_h":           int(r["nb_h"] or 0),
        "nb_f":           int(r["nb_f"] or 0),
        "best_h":         r["best_h"],
        "best_f":         r["best_f"],
        "avg_rank_h":     int(r["avg_rank_h"] or 0) if r["avg_rank_h"] else None,
        "avg_rank_f":     int(r["avg_rank_f"] or 0) if r["avg_rank_f"] else None,
        "nb_tournois_12m": int(r["nb_tournois_12m"] or 0),
    } for r in rows])
    try: _store_in_mem(_cache_key, resp.get_data())
    except Exception: pass
    return resp


@app.get("/api/stats/shared_positions")
def route_stats_shared_positions():
    """
    Détecte les tournois où plusieurs paires ont été placées à la même position
    (ex : 2 paires classées 7ème au lieu de 7ème et 8ème — favoritisme arbitre).
    Filtre optionnel : ?pos_max=8 (positions 1 à N), ?cat=P500
    Retourne :
      - total : nombre d'incidents
      - by_position : répartition par position
      - top_clubs : clubs avec le plus de bénéficiaires
      - incidents : liste (date desc) avec les paires impliquées
    """
    from db import fetchall
    from collections import defaultdict as _dd

    pos_max = min(int(request.args.get("pos_max", 32)), 64)
    cat_filter = request.args.get("cat", "").strip()

    # ── Récupérer tous les participants impliqués dans un incident ─────────
    # Chaque paire occupe 2 lignes dans participations (une par joueur).
    # Un incident = (id_tournoi, position) avec COUNT(*) > 2, soit 2+ paires distinctes.
    # On compte les paires via COUNT(DISTINCT pair_key) > 1 pour être robuste.
    cat_clause = f"AND t.categorie = ?" if cat_filter else ""
    params = [pos_max, pos_max]
    if cat_filter:
        params.append(cat_filter)
        params.append(cat_filter)

    rows = fetchall(f"""
        SELECT
            p.id_tournoi,
            t.nom        AS tournoi_nom,
            t.categorie,
            p.position,
            p.date_tournoi,
            p.id_joueur,
            p.partenaire_id,
            p.partenaire_nom,
            j.nom        AS joueur_nom,
            j.prenom     AS joueur_prenom,
            j.club_nom
        FROM participations p
        JOIN (
            -- Trouver les (tournoi, position) avec 2+ paires distinctes.
            -- Chaque paire = 2 lignes → COUNT(*) > 2 signifie 2+ paires.
            -- On ignore les positions non numériques ou > 3 chiffres.
            SELECT pi.id_tournoi, pi.position
            FROM participations pi
            JOIN tournois ti ON ti.id_tournoi = pi.id_tournoi
            WHERE pi.position IS NOT NULL
              AND CAST(pi.position AS INT) BETWEEN 1 AND ?
              AND LENGTH(TRIM(pi.position)) <= 3
              AND TRIM(pi.position) GLOB '[0-9]*'
              {cat_clause.replace('t.', 'ti.')}
            GROUP BY pi.id_tournoi, pi.position
            HAVING COUNT(*) > 2
        ) dup ON dup.id_tournoi = p.id_tournoi AND dup.position = p.position
        JOIN tournois t  ON t.id_tournoi  = p.id_tournoi
        JOIN joueurs  j  ON j.id_fft      = p.id_joueur
        WHERE p.position IS NOT NULL
          AND CAST(p.position AS INT) BETWEEN 1 AND ?
          AND LENGTH(TRIM(p.position)) <= 3
          AND TRIM(p.position) GLOB '[0-9]*'
          {cat_clause}
        ORDER BY p.date_tournoi DESC, p.id_tournoi, CAST(p.position AS INT) ASC
        LIMIT 10000
    """, params)

    # ── Regrouper par incident (tournoi + position) ───────────────────────
    incidents_dict = {}
    club_counts    = _dd(int)
    pos_counts     = _dd(int)

    for r in rows:
        key = (r["id_tournoi"], r["position"])
        if key not in incidents_dict:
            incidents_dict[key] = {
                "id_tournoi":  r["id_tournoi"],
                "tournoi_nom": r["tournoi_nom"] or "",
                "categorie":   r["categorie"]   or "",
                "date":        r["date_tournoi"] or "",
                "position":    r["position"],
                "paires":      [],
            }
            pos_counts[str(r["position"])] += 1
        club = r["club_nom"] or "?"
        incidents_dict[key]["paires"].append({
            "joueur":       f"{(r.get('joueur_prenom') or '').strip()} {r.get('joueur_nom') or ''}".strip(),
            "joueur_id":    r["id_joueur"],
            "partenaire":   r["partenaire_nom"] or "?",
            "partenaire_id": r["partenaire_id"] or "",
            "club":         club,
        })
        club_counts[club] += 1

    # ── Trier et limiter les incidents retournés ──────────────────────────
    incidents = sorted(incidents_dict.values(),
                       key=lambda x: (x["date"], x["position"]), reverse=True)[:500]

    top_clubs = sorted(
        [{"club": c, "nb": n} for c, n in club_counts.items()],
        key=lambda x: -x["nb"]
    )[:20]

    # Trier by_position de façon numérique
    by_position = dict(
        sorted(pos_counts.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 99)
    )

    return jsonify({
        "total":       len(incidents_dict),
        "by_position": by_position,
        "top_clubs":   top_clubs,
        "incidents":   incidents,
    })


@app.get("/api/health")
def route_health():
    return jsonify({"status": "ok"})


@app.get("/api/config")
def route_config():
    """Feature flags exposés au frontend — déterminés par l'environnement."""
    is_prod = USE_POSTGRES  # True sur Render, False en local SQLite
    return jsonify({
        "env":       "prod" if is_prod else "dev",
        "features": {
            # Graphe et carte désactivés — fonctionnalités en cours de développement
            "graph":          False,
            "map":            False,
            # Bandeau "en construction" toujours affiché pour graph/map
            "wip_banners":    is_prod,
            # Recherche : longueur min (3 en prod pour les index trigram, 2 en dev)
            "search_min_len": 3 if is_prod else 2,
        }
    })


# ── Endpoint admin : lance le pré-calcul des caches lourds ──────────────────
# Usage : GET /api/admin/precompute?key=XXXX
# La clé doit matcher la variable d'env ADMIN_KEY (à configurer dans Render).
# Le job tourne dans un thread daemon (le user reçoit "started" tout de suite,
# le calcul finit en ~5-10 min en arrière-plan).
_precompute_status = {"running": False, "last_run": None, "last_result": None}


@app.get("/api/admin/debug")
def route_admin_debug():
    """Diagnostic temporaire — vérifie les tables PG et teste une query profil."""
    admin_key = os.environ.get("ADMIN_KEY", "")
    if admin_key and request.args.get("key") != admin_key:
        return jsonify({"error": "clé invalide"}), 403
    from db import USE_POSTGRES, get_conn
    info = {"use_postgres": USE_POSTGRES, "tables": [], "classements_historique_cols": [], "substr_test": None, "profile_test": None}
    try:
        with get_conn() as conn:
            if USE_POSTGRES:
                import psycopg2.extras
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_name IN ('classements_historique','cache_responses','tournois_summary') ORDER BY table_name")
                    info["tables"] = [r["table_name"] for r in cur.fetchall()]
                    if "classements_historique" in info["tables"]:
                        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='classements_historique' ORDER BY ordinal_position")
                        info["classements_historique_cols"] = [r["column_name"] for r in cur.fetchall()]
                    cur.execute("SELECT SUBSTR(date_tournoi,7,4)||SUBSTR(date_tournoi,4,2)||SUBSTR(date_tournoi,1,2) AS ds FROM participations LIMIT 3")
                    info["substr_test"] = [r["ds"] for r in cur.fetchall()]
                    cur.execute("SELECT id_fft FROM joueurs WHERE classement IS NOT NULL LIMIT 1")
                    row = cur.fetchone()
                    if row:
                        pid = row["id_fft"]
                        try:
                            cur.execute("SELECT COUNT(*) AS n FROM participations p JOIN tournois t ON p.id_tournoi=t.id_tournoi WHERE p.id_joueur=%s", (pid,))
                            info["profile_test"] = {"player": pid, "nb_parts": cur.fetchone()["n"]}
                        except Exception as e:
                            info["profile_test"] = {"error": str(e)}
            else:
                info["tables"] = ["(SQLite local)"]
    except Exception as e:
        info["error"] = str(e)
    return jsonify(info)


@app.get("/api/admin/precompute")
def route_admin_precompute():
    admin_key = os.environ.get("ADMIN_KEY", "")
    if not admin_key:
        return jsonify({"error": "ADMIN_KEY non configurée côté serveur"}), 500
    if request.args.get("key") != admin_key:
        return jsonify({"error": "clé invalide"}), 403
    if _precompute_status["running"]:
        return jsonify({"status": "already_running", "started_at": _precompute_status["last_run"]})

    import threading as _th, datetime as _dt
    def _run():
        _precompute_status["running"] = True
        _precompute_status["last_run"] = _dt.datetime.now().isoformat()
        try:
            import precompute
            precompute.main()
            _precompute_status["last_result"] = "ok"
        except Exception as e:
            _precompute_status["last_result"] = f"error: {type(e).__name__}: {e}"
            import traceback; traceback.print_exc()
        finally:
            _precompute_status["running"] = False

    _th.Thread(target=_run, daemon=True, name="precompute-job").start()
    return jsonify({"status": "started",
                    "message": "Le pré-calcul tourne en arrière-plan (~5-10 min). "
                               "Surveille les logs Render pour voir les ✅ par job."})


@app.get("/api/admin/precompute/status")
def route_admin_precompute_status():
    return jsonify(_precompute_status)


# ══════════════════════════════════════════════════════════════════════════════
#  AUTH — Magic link
# ══════════════════════════════════════════════════════════════════════════════

def _get_session() -> dict | None:
    """Helper : résout X-Session-Id header → user dict ou None."""
    sid = request.headers.get("X-Session-Id", "").strip()
    return get_user_from_session(sid) if sid else None


@app.post("/api/auth/login")
def route_auth_login():
    """
    Body JSON : {"email": "..."}
    Génère un magic link.
    En mode dev (pas de SMTP) : retourne le lien directement dans la réponse.
    """
    data  = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return jsonify({"error": "Email invalide"}), 400
    try:
        result = create_magic_link(email)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(result)


@app.get("/api/auth/verify")
def route_auth_verify():
    """
    ?token=<magic_link_token>
    Valide le token, crée une session, retourne {"session_id", "email", "is_new", "player_fft_id"}.
    """
    token = request.args.get("token", "").strip()
    if not token:
        return jsonify({"error": "Token manquant"}), 400
    result = verify_token(token)
    if not result:
        return jsonify({"error": "Token invalide ou expiré"}), 401
    return jsonify(result)


@app.post("/api/auth/logout")
def route_auth_logout():
    sid = request.headers.get("X-Session-Id", "").strip()
    if sid:
        invalidate_session(sid)
    return jsonify({"ok": True})


# ══════════════════════════════════════════════════════════════════════════════
#  /api/me — Profil utilisateur connecté
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/me")
def route_me():
    user = _get_session()
    if not user:
        return jsonify({"error": "Non authentifié"}), 401

    # Si l'utilisateur a lié un profil FFT, on embed ses infos de base
    player_info = None
    if user.get("player_fft_id"):
        from player_profile import get_player_profile
        profile = get_player_profile(user["player_fft_id"])
        if profile:
            player_info = {
                "id":               profile["id"],
                "nom_complet":      profile["nom_complet"],
                "classement":       profile["classement"],
                "meilleur_classement": profile["meilleur_classement"],
                "variation_classement": profile.get("variation_classement"),
                "club":             profile["club"],
                "ville":            profile["ville"],
                "sexe":             profile["sexe"],
                "naissance_annee":  profile.get("naissance_annee"),
                "nb_tournois":      profile["nb_tournois"],
                "nb_victoires":     profile["nb_victoires"],
                "pos_moyenne":      profile["pos_moyenne"],
            }

    return jsonify({
        "user_id":       user["user_id"],
        "email":         user["email"],
        "display_name":  user.get("display_name"),
        "player_fft_id": user.get("player_fft_id"),
        "player":        player_info,
    })


@app.post("/api/me/player")
def route_me_link_player():
    """
    Body JSON : {"player_fft_id": "...", "display_name": "..."}
    Lie un profil FFT au compte connecté.
    """
    user = _get_session()
    if not user:
        return jsonify({"error": "Non authentifié"}), 401

    data          = request.get_json(silent=True) or {}
    player_fft_id = (data.get("player_fft_id") or "").strip()
    display_name  = (data.get("display_name") or "").strip() or None

    if not player_fft_id:
        return jsonify({"error": "player_fft_id requis"}), 400

    # Vérifier que le joueur existe
    from player_profile import get_player_profile
    profile = get_player_profile(player_fft_id)
    if not profile:
        return jsonify({"error": "Joueur introuvable"}), 404

    link_player(user["user_id"], player_fft_id,
                display_name or profile.get("nom_complet"))
    return jsonify({"ok": True, "player": {
        "id":          profile["id"],
        "nom_complet": profile["nom_complet"],
        "classement":  profile["classement"],
        "club":        profile["club"],
    }})


@app.delete("/api/me/player")
def route_me_unlink_player():
    user = _get_session()
    if not user:
        return jsonify({"error": "Non authentifié"}), 401
    if not user:
        return jsonify({"error": "Non authentifié"}), 401
    unlink_player(user["user_id"])
    return jsonify({"ok": True})


# ══════════════════════════════════════════════════════════════════════════════
#  /api/me/favorites — Favoris
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/me/favorites")
def route_me_favorites():
    user = _get_session()
    if not user:
        return jsonify({"error": "Non authentifié"}), 401
    return jsonify(get_favorites(user["user_id"]))


@app.post("/api/me/favorites/<player_id>")
def route_me_fav_add(player_id: str):
    user = _get_session()
    if not user:
        return jsonify({"error": "Non authentifié"}), 401
    add_favorite(user["user_id"], player_id)
    return jsonify({"ok": True, "favorited": True})


@app.delete("/api/me/favorites/<player_id>")
def route_me_fav_remove(player_id: str):
    user = _get_session()
    if not user:
        return jsonify({"error": "Non authentifié"}), 401
    remove_favorite(user["user_id"], player_id)
    return jsonify({"ok": True, "favorited": False})


@app.get("/api/me/favorites/<player_id>/check")
def route_me_fav_check(player_id: str):
    user = _get_session()
    if not user:
        return jsonify({"favorited": False})
    return jsonify({"favorited": is_favorite(user["user_id"], player_id)})


@app.get("/api/geo/filters")
def route_geo_filters():
    """Listes pour les selecteurs du classement : ligues (regions) et departements.
    Source : stats_geo_departement (table compacte, contient dept_num + comite + ligue)."""
    from db import fetchall
    try:
        rows = fetchall(
            "SELECT dept_num, comite, ligue FROM stats_geo_departement "
            "WHERE dept_num IS NOT NULL ORDER BY ligue, dept_num"
        )
    except Exception:
        rows = []
    departements = [
        {"dept_num": r["dept_num"], "comite": r.get("comite") or "", "ligue": r.get("ligue") or ""}
        for r in rows
    ]
    ligues = sorted({d["ligue"] for d in departements if d["ligue"]})
    return jsonify({"ligues": ligues, "departements": departements})


@app.get("/api/geo/departements")
def route_geo_departements():
    """Donnees choropletche par departement (H/F separes). Source stats_geo_departement."""
    from db import fetchall
    sexe = request.args.get("sexe", "H").upper()
    try:
        rows = fetchall(
            "SELECT dept_num, comite, ligue, nb_total, nb_h, nb_f, nb_clubs, "
            "classement_moyen, classement_moyen_h, classement_moyen_f "
            "FROM stats_geo_departement WHERE dept_num IS NOT NULL"
        )
    except Exception:
        rows = []
    out = []
    for r in rows:
        if sexe == "F":
            nb, clt = r.get("nb_f"), r.get("classement_moyen_f")
        elif sexe == "H":
            nb, clt = r.get("nb_h"), r.get("classement_moyen_h")
        else:
            nb, clt = r.get("nb_total"), r.get("classement_moyen")
        out.append({
            "dept_num": r["dept_num"], "comite": r.get("comite") or "",
            "ligue": r.get("ligue") or "", "nb": nb or 0,
            "classement_moyen": clt, "nb_clubs": r.get("nb_clubs") or 0,
        })
    return jsonify(out)


@app.get("/api/geo/clubs")
def route_geo_clubs():
    """Clubs geolocalises (marqueurs carte). Filtre optionnel par departement."""
    from db import fetchall
    dept = request.args.get("dept", "").strip()
    conds = ["lat IS NOT NULL", "lon IS NOT NULL"]
    params = []
    if dept:
        conds.append("dept_num = ?")
        params.append(dept)
    try:
        rows = fetchall(
            "SELECT id, nom, ville, dept_num, lat, lon FROM clubs WHERE "
            + " AND ".join(conds), tuple(params)
        )
    except Exception:
        rows = []
    return jsonify([
        {"id": r["id"], "nom": r["nom"] or "", "ville": r.get("ville") or "",
         "dept_num": r.get("dept_num") or "", "lat": r["lat"], "lon": r["lon"]}
        for r in rows
    ])


@app.route("/classement")
def route_classement_page():
    """Page classement H/F filtrable (region/departement/club). Donnees via /api/leaderboard."""
    return send_file(os.path.join(os.path.dirname(__file__), "classement.html"))


@app.get("/api/tournoi/<tid>")
def route_tournoi_api(tid: str):
    """Reconstruit le classement d'un tournoi : paires (2 joueurs) triées par position."""
    from db import fetchall, fetchone
    info = fetchone(
        """
        SELECT t.id_tournoi, t.nom, t.categorie,
               ts.niveau_points, ts.nb_joueurs, ts.classement_meilleur, ts.classement_moyen,
               tr.indice_niveau, tr.surcote_niveau, tr.indice_categorie, tr.niveau_effectif,
               tr.multi_board, tr.equipes, tr.nb_paires
        FROM tournois t
        LEFT JOIN tournois_stats  ts ON ts.id_tournoi = t.id_tournoi
        LEFT JOIN tournois_rating tr ON tr.id_tournoi = t.id_tournoi
        WHERE t.id_tournoi = ?
        """,
        (tid,),
    )
    if not info:
        return jsonify({"error": "Tournoi introuvable"}), 404
    rows = fetchall(
        """
        SELECT p.id_joueur, j.nom, j.prenom, j.classement, j.sexe, j.club_nom AS club,
               p.partenaire_id, p.partenaire_nom,
               jp.nom AS pnom, jp.prenom AS pprenom, jp.classement AS pclt, jp.club_nom AS pclub, jp.sexe AS psexe,
               p.position, p.position_num, p.points_num, p.date_tournoi, p.type
        FROM participations p
        JOIN joueurs j ON j.id_fft = p.id_joueur
        LEFT JOIN joueurs jp ON jp.id_fft = p.partenaire_id
        WHERE p.id_tournoi = ?
        """,
        (tid,),
    )

    date = next((r["date_tournoi"] for r in rows if r["date_tournoi"]), None)
    tmonth = (date[6:10] + "-" + date[3:5]) if date and len(date) >= 10 else None
    # Classement de chaque joueur AU MOMENT du tournoi (snapshot mensuel) ; fallback = classement actuel
    hist = {}
    if tmonth:
        _ids = set()
        for r in rows:
            _ids.add(str(r["id_joueur"]))
            if r["partenaire_id"]:
                _ids.add(str(r["partenaire_id"]))
        if _ids:
            _iph = ",".join("?" * len(_ids))
            for hr in fetchall(
                f"SELECT id_joueur, classement FROM classements_historique WHERE mois=? AND id_joueur IN ({_iph})",
                (tmonth,) + tuple(_ids)):
                if hr["classement"] is not None:
                    hist[str(hr["id_joueur"])] = hr["classement"]

    def _clt(idv, cur):
        return hist.get(str(idv), cur)

    def _nom(prenom, nom):
        return f"{(prenom or '').strip()} {(nom or '').strip()}".strip()

    def _sig(nm):
        return re.sub(r"[^a-z0-9]", "", (nm or "").lower())
    seen, pairs = set(), []
    for r in rows:
        pid = r["partenaire_id"]
        membres = [{"id": r["id_joueur"], "nom": _nom(r["prenom"], r["nom"]),
                    "classement": _clt(r["id_joueur"], r["classement"]), "club": r["club"], "sexe": r["sexe"]}]
        if pid:
            nomp = _nom(r["pprenom"], r["pnom"]) or (r["partenaire_nom"] or "")
            membres.append({"id": pid, "nom": (nomp if nomp and nomp != "None None" else "Partenaire inconnu"),
                            "classement": _clt(pid, r["pclt"]), "club": r["pclub"], "sexe": r["psexe"]})
        elif r["partenaire_nom"] and r["partenaire_nom"] != "None None":
            membres.append({"id": None, "nom": r["partenaire_nom"], "classement": None, "club": None, "sexe": None})
        # Dedup ROBUSTE par ensemble de noms (gere les doublons de fiche FFT pour une meme personne)
        sig = frozenset(_sig(m["nom"]) for m in membres if m.get("nom")) or frozenset({str(r["id_joueur"])})
        keyp = (r["position_num"], sig)
        if keyp in seen:
            continue
        seen.add(keyp)
        _sx = {m["sexe"] for m in membres if m.get("sexe")}
        psexe = "H" if _sx == {"H"} else ("F" if _sx == {"F"} else "X")
        pairs.append({
            "position": r["position"], "position_num": r["position_num"],
            "points": r["points_num"], "type": r["type"], "membres": membres, "sexe": psexe,
        })
    pairs.sort(key=lambda x: (x["position_num"] is None, x["position_num"] or 99999))
    return jsonify({"info": dict(info), "date": date, "mois": tmonth, "nb_paires_reelles": len(pairs), "pairs": pairs})


@app.get("/api/tournois/ranking")
def route_tournois_ranking():
    """Classement des tournois par niveau, triés par difficulté (indice ou surcote).
    Exclut multi-tableaux et épreuves par équipes (baseline propre)."""
    from db import fetchall
    niveau = request.args.get("niveau", "").strip()      # niveau_effectif (25..2000)
    sexe   = request.args.get("sexe", "").upper()
    sort   = request.args.get("sort", "indice")           # indice | surcote
    minp   = max(1, int(request.args.get("minp", 8)))
    limit  = min(int(request.args.get("limit", 50)), 200)
    offset = max(0, int(request.args.get("offset", 0)))

    conds  = ["tr.equipes = 0", "tr.multi_board = 0", "tr.nb_paires >= ?"]
    params = [minp]
    if niveau:
        conds.append("tr.niveau_effectif = ?"); params.append(int(niveau))
    if sexe in ("H", "F"):
        conds.append("tr.sexe = ?"); params.append(sexe)
    order = "tr.indice_categorie DESC" if sort == "categorie" else "tr.indice_niveau DESC"
    where = " AND ".join(conds)
    try:
        total = fetchall(f"SELECT COUNT(*) AS n FROM tournois_rating tr WHERE {where}", tuple(params))[0]["n"]
        rows = fetchall(
            f"""SELECT tr.id_tournoi, t.nom, tr.niveau_effectif, tr.sexe, tr.nb_paires,
                       tr.indice_niveau, tr.surcote_niveau, tr.indice_categorie
                FROM tournois_rating tr JOIN tournois t ON t.id_tournoi = tr.id_tournoi
                WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?""",
            tuple(params) + (limit, offset),
        )
        levels = [r["niveau_effectif"] for r in fetchall(
            "SELECT DISTINCT niveau_effectif FROM tournois_rating "
            "WHERE niveau_effectif IS NOT NULL ORDER BY niveau_effectif")]
    except Exception:
        total, rows, levels = 0, [], []
    return jsonify({"tournois": rows, "niveaux": levels, "total": total, "offset": offset})


@app.get("/api/clubs/tournois-ranking")
def route_clubs_tournois_ranking():
    """Clubs classés par difficulté moyenne des tournois qu'ils organisent.
    Rattachement tournoi→club via la table tournois_club (déduite du nom)."""
    from db import fetchall
    sexe   = request.args.get("sexe", "").upper()
    niveau = request.args.get("niveau", "").strip()
    mint   = max(1, int(request.args.get("mint", 5)))    # min tournois rattachés
    limit  = min(int(request.args.get("limit", 50)), 200)
    offset = max(0, int(request.args.get("offset", 0)))
    conds  = ["tr.equipes = 0", "tr.multi_board = 0"]
    params = []
    if sexe in ("H", "F"):
        conds.append("tr.sexe = ?"); params.append(sexe)
    if niveau:
        conds.append("tr.niveau_effectif = ?"); params.append(int(niveau))
    where = " AND ".join(conds)
    try:
        rows = fetchall(
            f"""SELECT tc.club_nom, COUNT(*) AS nb,
                       ROUND(AVG(tr.indice_niveau), 1) AS indice_moy,
                       MAX(tr.indice_niveau) AS indice_max
                FROM tournois_club tc
                JOIN tournois_rating tr ON tr.id_tournoi = tc.id_tournoi
                WHERE {where}
                GROUP BY tc.club_id
                HAVING COUNT(*) >= ?
                ORDER BY indice_moy DESC
                LIMIT ? OFFSET ?""",
            tuple(params) + (mint, limit, offset),
        )
        tot = fetchall(
            f"""SELECT COUNT(*) AS n FROM (
                  SELECT tc.club_id FROM tournois_club tc
                  JOIN tournois_rating tr ON tr.id_tournoi = tc.id_tournoi
                  WHERE {where} GROUP BY tc.club_id HAVING COUNT(*) >= ?)""",
            tuple(params) + (mint,),
        )
        total = tot[0]["n"] if tot else 0
    except Exception:
        rows, total = [], 0
    return jsonify({"clubs": rows, "total": total, "offset": offset})


@app.route("/clubs")
def route_clubs_page():
    """Classement des clubs par difficulté de leurs tournois."""
    return send_file(os.path.join(os.path.dirname(__file__), "clubs.html"))


@app.route("/tournois")
def route_tournois_page():
    """Classement des tournois par niveau (difficulté)."""
    return send_file(os.path.join(os.path.dirname(__file__), "tournois.html"))


@app.route("/tournoi/<tid>")
def route_tournoi_page(tid: str):
    """Vue tournoi : classement reconstruit avec les paires + difficulté."""
    return send_file(os.path.join(os.path.dirname(__file__), "tournoi.html"))


@app.get("/api/geo/departement/<dept>")
def route_geo_departement_detail(dept: str):
    """Détail d'un département : stats H/F, top joueurs, top clubs, tournois les plus relevés."""
    from db import fetchall, fetchone
    sexe = request.args.get("sexe", "H").upper()
    if sexe not in ("H", "F"):
        sexe = "H"
    info = fetchone(
        "SELECT dept_num, comite, ligue, nb_total, nb_h, nb_f, nb_clubs, "
        "classement_moyen, classement_moyen_h, classement_moyen_f "
        "FROM stats_geo_departement WHERE dept_num = ?", (dept,)
    )
    top_joueurs = fetchall(
        "SELECT id_fft, nom, prenom, classement, club_nom FROM joueurs "
        "WHERE dept_num = ? AND sexe = ? AND classement IS NOT NULL "
        "ORDER BY classement ASC LIMIT 6", (dept, sexe)
    )
    top_clubs = fetchall(
        "SELECT club_nom, COUNT(*) AS nb, ROUND(AVG(classement)) AS clt_moy, MIN(classement) AS best "
        "FROM joueurs WHERE dept_num = ? AND sexe = ? AND classement IS NOT NULL "
        "AND club_nom IS NOT NULL AND club_nom != '' "
        "GROUP BY club_nom HAVING COUNT(*) >= 3 ORDER BY clt_moy ASC LIMIT 6", (dept, sexe)
    )
    try:
        top_tournois = fetchall(
            """SELECT t.id_tournoi, t.nom, tr.niveau_effectif, tr.indice_niveau, tr.indice_categorie
               FROM tournois_club tc
               JOIN clubs cl            ON cl.id = tc.club_id
               JOIN tournois_rating tr  ON tr.id_tournoi = tc.id_tournoi
               JOIN tournois t          ON t.id_tournoi = tc.id_tournoi
               WHERE cl.dept_num = ? AND tr.sexe = ? AND tr.multi_board = 0 AND tr.equipes = 0
               ORDER BY tr.indice_niveau DESC LIMIT 5""", (dept, sexe)
        )
    except Exception:
        top_tournois = []
    return jsonify({
        "dept": dept, "sexe": sexe,
        "info": dict(info) if info else None,
        "top_joueurs": top_joueurs, "top_clubs": top_clubs, "top_tournois": top_tournois,
    })


@app.get("/api/geo/villes/<dept>")
def route_geo_villes(dept: str):
    """Stats par ville (commune) d'un département : nb joueurs + classement moyen (H/F)."""
    from db import fetchall
    sexe = request.args.get("sexe", "H").upper()
    if sexe not in ("H", "F"):
        sexe = "H"
    rows = fetchall(
        "SELECT ville, COUNT(*) AS nb, ROUND(AVG(classement)) AS clt_moy "
        "FROM joueurs WHERE dept_num = ? AND sexe = ? "
        "AND ville IS NOT NULL AND ville != '' AND classement IS NOT NULL "
        "GROUP BY ville", (dept, sexe)
    )
    return jsonify({"dept": dept, "sexe": sexe, "villes": rows})


@app.route("/carte")
def route_carte_page():
    """Carte : choropleche departements + marqueurs clubs."""
    return send_file(os.path.join(os.path.dirname(__file__), "carte.html"))


@app.route("/graphe")
def route_graphe_page():
    """Graphe de jeu + degres de separation."""
    return send_file(os.path.join(os.path.dirname(__file__), "graphe.html"))


@app.get("/api/club_bump")
def route_club_bump():
    """Bump chart : evolution du rang (entre eux) des membres d'un club sur ~10 mois.
    mode=niveau (top 30 par classement) ou actifs (30 plus actifs). H/F separes."""
    from db import fetchall
    import re as _re, unicodedata as _ud
    nom = request.args.get("nom", "").strip()
    if not nom:
        return jsonify({"error": "nom requis"}), 400
    sexe = request.args.get("sexe", "H").upper()
    if sexe not in ("H", "F"):
        sexe = "H"
    mode = request.args.get("mode", "niveau")
    if mode not in ("niveau", "actifs"):
        mode = "niveau"

    def _n(s):
        s = _ud.normalize("NFKD", s or "").encode("ascii", "ignore").decode().upper()
        return _re.sub(r"\s+", " ", _re.sub(r"[^A-Z0-9]", " ", s)).strip()
    key = _n(nom)
    like_op = "ILIKE" if USE_POSTGRES else "LIKE"
    cand = fetchall(f"SELECT DISTINCT club_nom FROM joueurs WHERE UPPER(club_nom) {like_op} ?", (f"%{key}%",))
    variants = [r["club_nom"] for r in cand if _n(r["club_nom"]) == key] or [nom]
    if nom not in variants:
        variants.append(nom)
    ph = ",".join("?" * len(variants))
    vt = tuple(variants)

    if mode == "actifs":
        members = fetchall(
            f"""SELECT j.id_fft, j.nom, j.prenom, j.classement, COUNT(p.id) AS nbp
                FROM joueurs j JOIN participations p ON p.id_joueur=j.id_fft
                WHERE j.club_nom IN ({ph}) AND j.sexe=?
                GROUP BY j.id_fft ORDER BY nbp DESC, j.classement ASC LIMIT 30""", vt + (sexe,))
    else:
        members = fetchall(
            f"""SELECT j.id_fft, j.nom, j.prenom, j.classement
                FROM joueurs j WHERE j.club_nom IN ({ph}) AND j.sexe=? AND j.classement IS NOT NULL
                ORDER BY j.classement ASC LIMIT 20""", vt + (sexe,))
    if not members:
        return jsonify({"months": [], "players": [], "mode": mode, "sexe": sexe})
    ids = [m["id_fft"] for m in members]

    mrows = fetchall("SELECT DISTINCT mois FROM classements_historique ORDER BY mois DESC LIMIT 10")
    months = sorted([r["mois"] for r in mrows])

    iph = ",".join("?" * len(ids))
    mph = ",".join("?" * len(months))
    clt = {}
    for r in fetchall(
        f"SELECT id_joueur, mois, classement FROM classements_historique "
        f"WHERE id_joueur IN ({iph}) AND mois IN ({mph}) AND classement IS NOT NULL",
        tuple(ids) + tuple(months)):
        clt[(str(r["id_joueur"]), r["mois"])] = int(r["classement"])

    pos = {m: {} for m in months}
    for m in months:
        present = [(i, clt[(str(i), m)]) for i in ids if (str(i), m) in clt]
        present.sort(key=lambda x: x[1])
        for rank, (i, _c) in enumerate(present, 1):
            pos[m][str(i)] = rank

    def _full(r):
        return f"{(r.get('prenom') or '').strip()} {(r.get('nom') or '').strip()}".strip()
    players = []
    for m0 in members:
        i = str(m0["id_fft"])
        players.append({
            "id": m0["id_fft"], "nom": _full(m0), "classement": m0.get("classement"),
            "pos": [pos[mo].get(i) for mo in months],
            "clt": [clt.get((i, mo)) for mo in months],
        })

    def _lastpos(pl):
        for v in reversed(pl["pos"]):
            if v is not None:
                return v
        return 999
    players.sort(key=_lastpos)
    return jsonify({"months": months, "players": players, "mode": mode, "sexe": sexe})


@app.get("/api/club_detail")
def route_club_detail():
    """Compléments page club : géo, âge/actifs, pools & top 10%, renouvellement,
    boss top 5 par sexe, championnats, tournois organisés."""
    from db import fetchall, fetchone
    import re as _re, unicodedata as _ud, math as _math
    nom = request.args.get("nom", "").strip()
    if not nom:
        return jsonify({"error": "nom requis"}), 400

    def _n(s):
        s = _ud.normalize("NFKD", s or "").encode("ascii", "ignore").decode().upper()
        return _re.sub(r"\s+", " ", _re.sub(r"[^A-Z0-9]", " ", s)).strip()

    key = _n(nom)
    like_op = "ILIKE" if USE_POSTGRES else "LIKE"
    cand = fetchall(
        f"SELECT DISTINCT club_nom FROM joueurs WHERE UPPER(club_nom) {like_op} ?",
        (f"%{key}%",),
    )
    variants = [r["club_nom"] for r in cand if _n(r["club_nom"]) == key] or [nom]
    if nom not in variants:
        variants.append(nom)
    ph = ",".join("?" * len(variants))
    vt = tuple(variants)

    def _full(r):
        return f"{(r.get('prenom') or '').strip()} {(r.get('nom') or '').strip()}".strip()

    # Géo + méta
    geo = None
    for r in fetchall(
        f"SELECT nom, ville, dept_num, comite, ligue, lat, lon FROM clubs WHERE UPPER(nom) {like_op} ?",
        (f"%{key}%",),
    ):
        if _n(r["nom"]) == key:
            geo = {"ville": r.get("ville") or "", "dept_num": r.get("dept_num") or "",
                   "comite": r.get("comite") or "", "ligue": r.get("ligue") or "",
                   "lat": r.get("lat"), "lon": r.get("lon")}
            break

    agg = fetchone(
        f"""SELECT ROUND(AVG(age),1) AS age_moyen,
                   SUM(CASE WHEN actif=1 THEN 1 ELSE 0 END) AS nb_actifs,
                   COUNT(*) AS tot
            FROM joueurs WHERE club_nom IN ({ph})""", vt) or {}
    tot = int(agg.get("tot") or 0)

    # Pools H/F (classés) -> seuils top 10 %, et nb de membres du club dans le top 10 %
    pools = {}
    for r in fetchall("SELECT sexe, COUNT(*) AS n FROM joueurs WHERE classement IS NOT NULL AND sexe IN ('H','F') GROUP BY sexe"):
        pools[r["sexe"]] = int(r["n"] or 0)

    def _top10(sexe):
        thr = int(_math.ceil(pools.get(sexe, 0) * 0.10))
        if thr <= 0:
            return 0
        r = fetchone(
            f"SELECT COUNT(*) AS n FROM joueurs WHERE club_nom IN ({ph}) AND sexe=? AND classement IS NOT NULL AND classement<=?",
            vt + (sexe, thr))
        return int((r or {}).get("n") or 0)

    top10 = {"h": _top10("H"), "f": _top10("F")}

    # Renouvellement : année de 1re participation
    ren = fetchall(
        f"""SELECT annee, COUNT(*) AS n FROM (
              SELECT j.id_fft, MIN(SUBSTR(p.date_tournoi,7,4)) AS annee
              FROM joueurs j JOIN participations p ON p.id_joueur=j.id_fft
              WHERE j.club_nom IN ({ph}) AND LENGTH(p.date_tournoi)>=10
              GROUP BY j.id_fft) GROUP BY annee""", vt)
    by_year = {r["annee"]: int(r["n"] or 0) for r in ren if r["annee"]}
    avec = sum(by_year.values())
    renouvellement = {"depuis_2025": by_year.get("2025", 0),
                      "nouveaux_2026": by_year.get("2026", 0),
                      "sans_match": max(0, tot - avec)}

    # Boss de l'arene : top 5 par sexe (victoires puis assiduite). H/F separes (regle d'or).
    def _boss(sexe):
        return [
            {"id": r["id_fft"], "nom_complet": _full(r),
             "points": int(r["points"] or 0), "tournois": int(r["tournois"] or 0)}
            for r in fetchall(
                f"""SELECT j.id_fft, j.nom, j.prenom,
                           COUNT(p.id) AS tournois,
                           COALESCE(SUM(p.points_num),0) AS points
                    FROM joueurs j JOIN participations p ON p.id_joueur=j.id_fft
                    WHERE j.club_nom IN ({ph}) AND j.sexe=?
                    GROUP BY j.id_fft
                    ORDER BY points DESC, tournois DESC LIMIT 10""", vt + (sexe,))
        ]
    boss = {"h": _boss("H"), "f": _boss("F")}

    championnats = [
        {"id_tournoi": r["id_tournoi"], "nom": r["nom"] or "", "categorie": r["categorie"] or "",
         "best": r.get("best"), "nb_joueurs": int(r["nbj"] or 0)}
        for r in fetchall(
            f"""SELECT t.id_tournoi, t.nom, t.categorie,
                       MIN(p.position_num) AS best, COUNT(DISTINCT p.id_joueur) AS nbj
                FROM joueurs j JOIN participations p ON p.id_joueur=j.id_fft
                JOIN tournois t ON t.id_tournoi=p.id_tournoi
                WHERE j.club_nom IN ({ph})
                  AND (t.categorie LIKE '%hampionnat%' OR t.categorie LIKE '%quipe%')
                GROUP BY t.id_tournoi ORDER BY best ASC LIMIT 10""", vt)
    ]

    club_ids = [r["id"] for r in fetchall(
        f"SELECT id, nom FROM clubs WHERE UPPER(nom) {like_op} ?", (f"%{key}%",)) if _n(r["nom"]) == key]
    tournois_orga = []
    if club_ids:
        iph = ",".join("?" * len(club_ids))
        tournois_orga = [
            {"id_tournoi": r["id_tournoi"], "nom": r["nom"] or "", "niveau": r.get("niveau_effectif"),
             "indice": r.get("indice_niveau"), "indice_categorie": r.get("indice_categorie")}
            for r in fetchall(
                f"""SELECT tc.id_tournoi, t.nom, tr.niveau_effectif, tr.indice_niveau, tr.indice_categorie
                    FROM tournois_club tc JOIN tournois t ON t.id_tournoi=tc.id_tournoi
                    LEFT JOIN tournois_rating tr ON tr.id_tournoi=tc.id_tournoi
                    WHERE tc.club_id IN ({iph})
                    ORDER BY (tr.indice_niveau IS NULL), tr.indice_niveau DESC LIMIT 12""",
                tuple(club_ids))
        ]

    # --- Binômes : duos qui reviennent le plus dans les tournois ORGANISÉS par le club ---
    binomes = []
    if club_ids:
        _iph2 = ",".join("?" * len(club_ids))
        pair_rows = fetchall(
            f"""SELECT CASE WHEN p.id_joueur<p.partenaire_id THEN p.id_joueur ELSE p.partenaire_id END AS a,
                       CASE WHEN p.id_joueur<p.partenaire_id THEN p.partenaire_id ELSE p.id_joueur END AS b,
                       COUNT(DISTINCT p.id_tournoi) AS nb, MIN(p.position_num) AS best
                FROM participations p
                WHERE p.id_tournoi IN (SELECT id_tournoi FROM tournois_club WHERE club_id IN ({_iph2}))
                  AND p.partenaire_id IS NOT NULL AND p.partenaire_id!=''
                GROUP BY a, b ORDER BY nb DESC, best ASC LIMIT 10""", tuple(club_ids))
        _pids = set()
        for r in pair_rows:
            _pids.add(r["a"]); _pids.add(r["b"])
        _names = {}
        if _pids:
            _iph = ",".join("?" * len(_pids))
            for r in fetchall(f"SELECT id_fft, nom, prenom FROM joueurs WHERE id_fft IN ({_iph})", tuple(_pids)):
                _names[r["id_fft"]] = _full(r)
        binomes = [{"a_id": r["a"], "b_id": r["b"], "a": _names.get(r["a"], "?"), "b": _names.get(r["b"], "?"),
                    "nb": int(r["nb"] or 0), "best": r["best"]} for r in pair_rows]

    # --- Activité mensuelle (participations des membres) ---
    activite = [{"mois": r["ym"], "n": int(r["n"] or 0)} for r in fetchall(
        f"""SELECT SUBSTR(p.date_tournoi,7,4)||'-'||SUBSTR(p.date_tournoi,4,2) AS ym, COUNT(*) AS n
            FROM participations p JOIN joueurs j ON j.id_fft=p.id_joueur
            WHERE j.club_nom IN ({ph}) AND LENGTH(p.date_tournoi)>=10
            GROUP BY ym ORDER BY ym""", vt)]

    # --- Progression RELATIVE (variation du club vs moyenne nationale = neutralise l'inflation) ---
    prog = None
    _mr = fetchone("SELECT MAX(mois) AS m FROM classements_historique")
    if _mr and _mr.get("m"):
        _latest = _mr["m"]
        _g = fetchone("SELECT AVG(variation) AS a FROM classements_historique WHERE mois=? AND variation IS NOT NULL", (_latest,))
        _cl = fetchone(
            f"""SELECT AVG(h.variation) AS a, COUNT(*) AS n,
                       SUM(CASE WHEN h.variation>0 THEN 1 ELSE 0 END) AS up
                FROM classements_historique h JOIN joueurs j ON j.id_fft=h.id_joueur
                WHERE h.mois=? AND j.club_nom IN ({ph}) AND h.variation IS NOT NULL""", (_latest,) + vt)
        if _cl and _cl.get("n"):
            prog = {"mois": _latest, "relatif": round((_cl["a"] or 0) - ((_g or {}).get("a") or 0)),
                    "up": int(_cl["up"] or 0), "n": int(_cl["n"])}

    # --- Comparaison au département ---
    dept_compare = None
    if geo and geo.get("dept_num"):
        _dn = geo["dept_num"]
        _da = {r["sexe"]: r["a"] for r in fetchall(
            "SELECT sexe, ROUND(AVG(classement)) AS a FROM joueurs WHERE dept_num=? AND classement IS NOT NULL AND sexe IN ('H','F') GROUP BY sexe", (_dn,))}
        _sz = fetchone("SELECT AVG(cnt) AS a FROM (SELECT club_nom, COUNT(*) AS cnt FROM joueurs WHERE dept_num=? AND club_nom IS NOT NULL AND club_nom!='' GROUP BY club_nom)", (_dn,))
        dept_compare = {"dept_num": _dn, "avg_h": _da.get("H"), "avg_f": _da.get("F"),
                        "club_size_avg": round(_sz["a"]) if _sz and _sz.get("a") else None}


    # --- Stats des tournois organisés (synthèse compacte) ---
    tournois_stats = None
    if club_ids:
        _tph = ",".join("?" * len(club_ids))
        _total = fetchone(f"SELECT COUNT(*) AS n FROM tournois_club WHERE club_id IN ({_tph})", tuple(club_ids))
        _rows = fetchall(
            f"""SELECT tr.niveau_effectif AS niv, COUNT(*) AS n, AVG(tr.indice_niveau) AS ind
                FROM tournois_club tc JOIN tournois_rating tr ON tr.id_tournoi=tc.id_tournoi
                WHERE tc.club_id IN ({_tph}) AND COALESCE(tr.equipes,0)=0 AND tr.niveau_effectif IS NOT NULL
                GROUP BY tr.niveau_effectif ORDER BY tr.niveau_effectif""", tuple(club_ids))
        _num = sum((r["ind"] or 0) * (r["n"] or 0) for r in _rows)
        _den = sum((r["n"] or 0) for r in _rows)
        tournois_stats = {
            "total": int((_total or {}).get("n") or 0),
            "by_niveau": [{"niv": r["niv"], "n": int(r["n"] or 0)} for r in _rows],
            "indice_moy": round(_num / _den) if _den else None,
        }

    # --- Meilleurs joueurs ayant JOUÉ un tournoi du club (pas forcément licenciés) ---
    joue_ici = {"h": [], "f": []}
    if club_ids:
        _jph = ",".join("?" * len(club_ids))
        _jtids = [r["id_tournoi"] for r in fetchall(
            f"SELECT id_tournoi FROM tournois_club WHERE club_id IN ({_jph})", tuple(club_ids))]
        if _jtids:
            _jtiph = ",".join("?" * len(_jtids))
            for _sx, _k in (("H", "h"), ("F", "f")):
                joue_ici[_k] = [
                    {"id": r["id_fft"], "nom_complet": _full(r), "classement": r["classement"]}
                    for r in fetchall(
                        f"""SELECT j.id_fft, j.nom, j.prenom, j.classement
                            FROM participations p JOIN joueurs j ON j.id_fft = p.id_joueur
                            WHERE p.id_tournoi IN ({_jtiph}) AND j.sexe=? AND j.classement IS NOT NULL
                            GROUP BY j.id_fft ORDER BY j.classement ASC LIMIT 50""",
                        tuple(_jtids) + (_sx,))
                ]

    return jsonify({
        "nom": nom, "geo": geo,
        "age_moyen": agg.get("age_moyen"), "nb_actifs": int(agg.get("nb_actifs") or 0), "nb_total": tot,
        "pools": {"h": pools.get("H", 0), "f": pools.get("F", 0)},
        "top10": top10,
        "renouvellement": renouvellement,
        "boss": boss,
        "championnats": championnats,
        "tournois_organises": tournois_orga,
        "tournois_stats": tournois_stats,
        "joue_ici": joue_ici,
        "binomes": binomes,
        "activite": activite,
        "progression": prog,
        "dept_compare": dept_compare,
    })


@app.route("/club")
def route_club_page():
    """Page dédiée d'un club (données via /api/club?nom= et /api/club_detail?nom=)."""
    return send_file(os.path.join(os.path.dirname(__file__), "club.html"))


@app.get("/api/search_all")
def route_search_all():
    """Recherche unifiée pour la barre : joueurs + clubs + villes."""
    from db import fetchall
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify({"joueurs": [], "clubs": [], "villes": []})
    op = "ILIKE" if USE_POSTGRES else "LIKE"
    joueurs = search_players(q, limit=6)
    clubs = [{"nom": r["club_nom"], "nb": int(r["n"] or 0)} for r in fetchall(
        f"""SELECT club_nom, COUNT(*) AS n FROM joueurs
            WHERE club_nom IS NOT NULL AND club_nom!='' AND UPPER(club_nom) {op} UPPER(?)
            GROUP BY club_nom ORDER BY n DESC LIMIT 5""", ("%" + q + "%",))]
    villes = [{"nom": r["ville"], "dept_num": r.get("dept_num"), "nb": int(r["n"] or 0)} for r in fetchall(
        f"""SELECT ville, dept_num, COUNT(*) AS n FROM joueurs
            WHERE ville IS NOT NULL AND ville!='' AND UPPER(ville) {op} UPPER(?)
            GROUP BY ville, dept_num ORDER BY n DESC LIMIT 5""", (q + "%",))]
    tournois = [{"id": r["id_tournoi"], "nom": r["nom"] or "", "niveau": r.get("niveau_effectif")} for r in fetchall(
        f"""SELECT t.id_tournoi, t.nom, tr.niveau_effectif FROM tournois t
            LEFT JOIN tournois_rating tr ON tr.id_tournoi = t.id_tournoi
            WHERE t.nom IS NOT NULL AND UPPER(t.nom) {op} UPPER(?) LIMIT 6""", ("%" + q + "%",))]
    return jsonify({"joueurs": joueurs, "clubs": clubs, "villes": villes, "tournois": tournois})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_ENV") != "production"
    app.run(host="0.0.0.0", port=port, debug=debug, use_reloader=False)
