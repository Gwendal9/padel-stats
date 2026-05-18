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

# Chargement asynchrone du graphe — le site démarre immédiatement,
# le graphe se charge en arrière-plan (les index PG créés dans ensure_indexes
# rendent la requête ~10x plus rapide qu'au démarrage précédent).
import threading as _threading
_threading.Thread(target=engine._ensure_loaded, daemon=True, name="graph-preloader").start()


@app.route("/")
def index():
    html_path = os.path.join(os.path.dirname(__file__), "..", "dashboard_mockup.html")
    return send_file(os.path.abspath(html_path))


@app.get("/api/search")
def route_search():
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify([])
    limit = min(int(request.args.get("limit", 20)), 50)
    return jsonify(search_players(q, limit=limit))


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


def _graph_ready():
    """Retourne une réponse 503 si le graphe n'est pas encore chargé, None sinon."""
    if not engine._loaded:
        return jsonify({"error": "graph_loading", "message": "Graphe en cours de chargement, réessaie dans quelques secondes"}), 503
    return None

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


@app.get("/api/stats")
def route_stats():
    from db import fetchall, fetchone
    current_year = datetime.date.today().year

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
    except Exception:
        last_12 = []

    villes = fetchall("""
        SELECT UPPER(TRIM(ville)) AS ville, COUNT(*) AS nb
        FROM joueurs WHERE ville IS NOT NULL AND ville != ''
        GROUP BY UPPER(TRIM(ville))
        ORDER BY nb DESC LIMIT 10
    """)

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
    except Exception:
        tdist = [0, 0, 0, 0, 0, 0]

    return jsonify({
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

    where = " AND ".join(conditions)
    total_row = fetchone("SELECT COUNT(*) AS n FROM joueurs j WHERE " + where, tuple(params))
    total = total_row["n"] if total_row else 0

    rows = fetchall(
        "SELECT j.id_fft, j.nom, j.prenom, j.classement, j.meilleur_classement,"
        " j.variation_classement, j.classement_date,"
        " j.club_nom, j.ville, j.sexe, j.naissance,"
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
    return jsonify([
        {"nom": e["nom"], "ville": e["ville"], "nb": e["nb"],
         "variants": e["variants"]}
        for e in result
    ])


@app.get("/api/tournaments")
def route_tournaments():
    from db import fetchall
    q     = request.args.get("q", "").strip()
    limit = min(int(request.args.get("limit", 20)), 50)
    # Tri chronologique: dates stockées en DD/MM/YYYY → besoin de TO_DATE en PG
    _date_sort = "TO_DATE(MIN(p.date_tournoi), 'DD/MM/YYYY') DESC" if USE_POSTGRES else \
                 "SUBSTR(MIN(p.date_tournoi),7,4)||SUBSTR(MIN(p.date_tournoi),4,2)||SUBSTR(MIN(p.date_tournoi),1,2) DESC"
    # Exclure les championnats et épreuves (pas des tournois padel classiques)
    _like = "ILIKE" if USE_POSTGRES else "LIKE"
    _excl = (f"t.nom NOT {_like} 'CHAMPIONNAT%' AND t.nom NOT {_like} 'EPREUVE%'"
             f" AND t.nom NOT {_like} '%CHAMPIONNAT%' AND t.nom NOT {_like} '%EPREUVE%'"
             f" AND (t.categorie IS NULL OR (t.categorie NOT {_like} 'CHAMP%'"
             f"   AND t.categorie NOT {_like} 'EPRE%'))")
    if q:
        rows = fetchall(f"""
            SELECT t.id_tournoi, t.nom, t.categorie,
                   MIN(p.date_tournoi) AS date_tournoi,
                   COUNT(DISTINCT p.id_joueur) AS nb_joueurs
            FROM tournois t
            JOIN participations p ON p.id_tournoi = t.id_tournoi
            WHERE t.nom {_like} ?
              AND {_excl}
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
            WHERE {_excl}
            GROUP BY t.id_tournoi, t.nom, t.categorie
            ORDER BY {_date_sort} LIMIT ?
        """, (limit,))
    return jsonify([{
        "id": r["id_tournoi"], "nom": r["nom"] or "", "categorie": r["categorie"] or "",
        "date": r["date_tournoi"] or "", "nb_joueurs": r["nb_joueurs"] or 0,
    } for r in rows])


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
    from db import fetchall

    # Opérateur LIKE adapté au moteur (ILIKE = insensible à la casse en PG)
    _lk = "ILIKE" if USE_POSTGRES else "LIKE"
    # Exclusion des championnats et épreuves (sur le nom ET la catégorie)
    _excl = (
        f"t.nom NOT {_lk} '%CHAMPIONNAT%' AND t.nom NOT {_lk} '%EPREUVE%' "
        f"AND t.categorie NOT {_lk} '%CHAMP%' AND t.categorie NOT {_lk} '%EPRE%'"
    )

    # Stats de taille par catégorie
    cat_stats = fetchall(f"""
        SELECT
          t.categorie,
          COUNT(DISTINCT t.id_tournoi)                                    AS nb_tournois,
          ROUND(AVG(sub.nb_joueurs))                                      AS avg_joueurs,
          MIN(sub.nb_joueurs)                                             AS min_joueurs,
          MAX(sub.nb_joueurs)                                             AS max_joueurs
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

    # Top villes par catégorie : on cherche la ville la plus fréquente des joueurs
    # qui ont participé à des tournois de cette catégorie
    # (approximation : ville du club du joueur ≈ ville du tournoi)
    city_rows = fetchall(f"""
        SELECT
          t.categorie,
          j.ville,
          COUNT(DISTINCT t.id_tournoi) AS nb
        FROM tournois t
        JOIN participations p ON p.id_tournoi = t.id_tournoi
        JOIN joueurs j ON j.id_fft = p.id_joueur
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

    return jsonify(result)


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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_ENV") != "production"
    app.run(host="0.0.0.0", port=port, debug=debug, use_reloader=False)
