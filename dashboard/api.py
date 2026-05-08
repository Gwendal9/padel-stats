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

app = Flask(__name__)
CORS(app)

# Préchargement du graphe en arrière-plan dès le démarrage
# Avec gunicorn --preload, le thread tourne dans le master avant le fork
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


@app.get("/api/path/<src_id>/<tgt_id>")
def route_path(src_id: str, tgt_id: str):
    result = engine.shortest_path(src_id, tgt_id)
    if result is None:
        return jsonify({"error": "Aucun chemin trouve"}), 404
    return jsonify(result)


@app.get("/api/ego/<player_id>")
def route_ego(player_id: str):
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
          (SELECT COUNT(*) FROM joueurs)                    AS nb_joueurs,
          (SELECT COUNT(DISTINCT id_tournoi) FROM participations) AS nb_tournois,
          (SELECT COUNT(*) FROM participations)             AS nb_participations
    """) or {}

    ranking = fetchone("""
        SELECT
          SUM(CASE WHEN classement <= 100 THEN 1 ELSE 0 END)                AS top100,
          SUM(CASE WHEN classement BETWEEN 101 AND 1000 THEN 1 ELSE 0 END)  AS c100_1k,
          SUM(CASE WHEN classement BETWEEN 1001 AND 5000 THEN 1 ELSE 0 END) AS c1k_5k,
          SUM(CASE WHEN classement BETWEEN 5001 AND 20000 THEN 1 ELSE 0 END) AS c5k_20k,
          SUM(CASE WHEN classement BETWEEN 20001 AND 40000 THEN 1 ELSE 0 END) AS c20k_40k,
          SUM(CASE WHEN classement BETWEEN 40001 AND 80000 THEN 1 ELSE 0 END) AS c40k_80k,
          SUM(CASE WHEN classement > 80000 THEN 1 ELSE 0 END)               AS c80kplus
        FROM joueurs WHERE classement IS NOT NULL
    """) or {}

    # Pyramide âges — agrégée en SQL (évite 149k lignes en Python)
    _yr = current_year
    pyr_rows = fetchall(f"""
        SELECT sexe,
          SUM(CASE WHEN ({_yr} - naissance::int) < 18                        THEN 1 ELSE 0 END) AS b0,
          SUM(CASE WHEN ({_yr} - naissance::int) BETWEEN 18 AND 25           THEN 1 ELSE 0 END) AS b1,
          SUM(CASE WHEN ({_yr} - naissance::int) BETWEEN 26 AND 35           THEN 1 ELSE 0 END) AS b2,
          SUM(CASE WHEN ({_yr} - naissance::int) BETWEEN 36 AND 45           THEN 1 ELSE 0 END) AS b3,
          SUM(CASE WHEN ({_yr} - naissance::int) BETWEEN 46 AND 55           THEN 1 ELSE 0 END) AS b4,
          SUM(CASE WHEN ({_yr} - naissance::int) BETWEEN 56 AND 65           THEN 1 ELSE 0 END) AS b5,
          SUM(CASE WHEN ({_yr} - naissance::int) > 65                        THEN 1 ELSE 0 END) AS b6
        FROM joueurs
        WHERE naissance IS NOT NULL
          AND naissance ~ '^[0-9]{{4}}$'
          AND sexe IN ('H','F')
        GROUP BY sexe
    """) if USE_POSTGRES else fetchall(
        "SELECT sexe, naissance FROM joueurs WHERE naissance IS NOT NULL AND sexe IN ('H','F')"
    )
    pyramid = {"H": [0] * 7, "F": [0] * 7}
    if USE_POSTGRES:
        for r in pyr_rows:
            s = r.get("sexe")
            if s in pyramid:
                pyramid[s] = [int(r.get(f"b{i}") or 0) for i in range(7)]
    else:
        for r in pyr_rows:
            try:
                age = _yr - int(r["naissance"])
            except (ValueError, TypeError):
                continue
            s = r.get("sexe")
            if s not in pyramid:
                continue
            if age < 18:      b = 0
            elif age <= 25:   b = 1
            elif age <= 35:   b = 2
            elif age <= 45:   b = 3
            elif age <= 55:   b = 4
            elif age <= 65:   b = 5
            else:             b = 6
            pyramid[s][b] += 1

    try:
        if USE_POSTGRES:
            month_rows = fetchall("""
                SELECT TO_CHAR(DATE_TRUNC('month', date_tournoi::date), 'Mon YYYY') AS mois,
                       DATE_TRUNC('month', date_tournoi::date) AS mois_sort,
                       COUNT(DISTINCT id_tournoi) AS nb
                FROM participations
                WHERE date_tournoi IS NOT NULL
                  AND date_tournoi ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
                GROUP BY DATE_TRUNC('month', date_tournoi::date)
                ORDER BY DATE_TRUNC('month', date_tournoi::date) DESC
                LIMIT 12
            """)
            last_12 = [(r["mois"], r["nb"]) for r in reversed(month_rows)]
        else:
            date_rows = fetchall(
                "SELECT DISTINCT id_tournoi, date_tournoi FROM participations WHERE date_tournoi IS NOT NULL"
            )
            monthly = defaultdict(int)
            for r in date_rows:
                d = r.get("date_tournoi") or ""
                try:
                    if len(d) == 10 and d[2] == "/":
                        mois = d[3:5] + "/" + d[6:]
                    elif len(d) == 10 and d[4] == "-":
                        mois = d[5:7] + "/" + d[:4]
                    else:
                        continue
                    monthly[mois] += 1
                except Exception:
                    continue
            MONTH_ABBR = {
                '01':'Jan','02':'Fév','03':'Mar','04':'Avr','05':'Mai','06':'Jun',
                '07':'Jul','08':'Aoû','09':'Sep','10':'Oct','11':'Nov','12':'Déc'
            }
            def _sort_key(m):
                mm, yyyy = m.split("/")
                return (int(yyyy), int(mm))
            raw_12 = sorted(monthly.items(), key=lambda x: _sort_key(x[0]))[-12:]
            last_12 = [(MONTH_ABBR.get(m.split('/')[0], m.split('/')[0]) + ' ' + m.split('/')[1], nb)
                       for m, nb in raw_12]
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
            tourn_sizes = fetchall(
                "SELECT id_tournoi, COUNT(*) AS nb_parts FROM participations GROUP BY id_tournoi"
            )
            tdist = [0, 0, 0, 0, 0, 0]
            for r in tourn_sizes:
                pairs = (r.get("nb_parts") or 0) // 2
                if pairs <= 8:      tdist[0] += 1
                elif pairs <= 16:   tdist[1] += 1
                elif pairs <= 32:   tdist[2] += 1
                elif pairs <= 64:   tdist[3] += 1
                elif pairs <= 128:  tdist[4] += 1
                else:               tdist[5] += 1
    except Exception:
        tdist = [0, 0, 0, 0, 0, 0]

    return jsonify({
        "nb_joueurs":        int(counts.get("nb_joueurs") or 0),
        "nb_tournois":       int(counts.get("nb_tournois") or 0),
        "nb_participations": int(counts.get("nb_participations") or 0),
        "ranking_dist": [
            ranking.get("top100", 0), ranking.get("c100_1k", 0),
            ranking.get("c1k_5k", 0), ranking.get("c5k_20k", 0),
            ranking.get("c20k_40k", 0), ranking.get("c40k_80k", 0),
            ranking.get("c80kplus", 0),
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

    sexe   = request.args.get("sexe", "H").upper()
    club   = request.args.get("club", "").strip()
    q      = request.args.get("q", "").strip()
    age    = request.args.get("age", "")
    offset = max(0, int(request.args.get("offset", 0)))
    limit  = min(int(request.args.get("limit", 50)), 100)

    conditions = ["j.classement IS NOT NULL"]
    params = []

    if sexe in ("H", "F"):
        conditions.append("j.sexe = ?")
        params.append(sexe)

    ville  = request.args.get("ville", "").strip()

    if club:
        conditions.append("j.club_nom LIKE ?")
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
        " j.club_nom, j.ville, j.sexe, j.naissance,"
        " (SELECT COUNT(*) FROM participations p WHERE p.id_joueur = j.id_fft) AS nb_tournois"
        " FROM joueurs j WHERE " + where +
        " ORDER BY j.classement ASC LIMIT ? OFFSET ?",
        tuple(params) + (limit, offset),
    )

    def fmt(r):
        age_val = None
        try:
            if r.get("naissance") and str(r["naissance"]).isdigit():
                age_val = current_year - int(r["naissance"])
        except Exception:
            pass
        prenom = (r.get("prenom") or "").strip()
        nom    = r.get("nom") or ""
        return {
            "id":                  r["id_fft"],
            "nom":                 nom,
            "prenom":              prenom,
            "nom_complet":         (prenom + " " + nom).strip(),
            "classement":          r["classement"],
            "meilleur_classement": r["meilleur_classement"],
            "club":                r["club_nom"] or "",
            "ville":               r["ville"] or "",
            "sexe":                r["sexe"] or "",
            "age":                 age_val,
            "nb_tournois":         r["nb_tournois"] or 0,
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


@app.get("/api/clubs")
def route_clubs():
    from db import fetchall
    top = min(int(request.args.get("top", 100)), 500)
    q   = request.args.get("q", "").strip()
    if q:
        rows = fetchall("""
            SELECT club_nom, ville, COUNT(*) AS nb_joueurs
            FROM joueurs
            WHERE club_nom IS NOT NULL AND club_nom != ''
              AND ville    IS NOT NULL AND ville    != ''
              AND club_nom LIKE ?
            GROUP BY club_nom, ville
            ORDER BY nb_joueurs DESC LIMIT ?
        """, ("%" + q + "%", top))
    else:
        rows = fetchall("""
            SELECT club_nom, ville, COUNT(*) AS nb_joueurs
            FROM joueurs
            WHERE club_nom IS NOT NULL AND club_nom != ''
              AND ville    IS NOT NULL AND ville    != ''
            GROUP BY club_nom, ville
            ORDER BY nb_joueurs DESC LIMIT ?
        """, (top,))
    return jsonify([{"nom": r["club_nom"], "ville": r["ville"], "nb": r["nb_joueurs"]} for r in rows])


@app.get("/api/tournaments")
def route_tournaments():
    from db import fetchall
    q     = request.args.get("q", "").strip()
    limit = min(int(request.args.get("limit", 20)), 50)
    if q:
        rows = fetchall("""
            SELECT t.id_tournoi, t.nom, t.categorie,
                   MIN(p.date_tournoi) AS date_tournoi,
                   COUNT(DISTINCT p.id_joueur) AS nb_joueurs
            FROM tournois t
            JOIN participations p ON p.id_tournoi = t.id_tournoi
            WHERE t.nom ILIKE ?
            GROUP BY t.id_tournoi, t.nom, t.categorie
            ORDER BY date_tournoi DESC LIMIT ?
        """, ("%" + q + "%", limit)) if USE_POSTGRES else fetchall("""
            SELECT t.id_tournoi, t.nom, t.categorie,
                   MIN(p.date_tournoi) AS date_tournoi,
                   COUNT(DISTINCT p.id_joueur) AS nb_joueurs
            FROM tournois t
            JOIN participations p ON p.id_tournoi = t.id_tournoi
            WHERE t.nom LIKE ?
            GROUP BY t.id_tournoi, t.nom, t.categorie
            ORDER BY date_tournoi DESC LIMIT ?
        """, ("%" + q + "%", limit))
    else:
        rows = fetchall("""
            SELECT t.id_tournoi, t.nom, t.categorie,
                   MIN(p.date_tournoi) AS date_tournoi,
                   COUNT(DISTINCT p.id_joueur) AS nb_joueurs
            FROM tournois t
            JOIN participations p ON p.id_tournoi = t.id_tournoi
            GROUP BY t.id_tournoi, t.nom, t.categorie
            ORDER BY date_tournoi DESC LIMIT ?
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

    # Deduplicate pairs (same position + same pair appears twice)
    seen = set()
    pairs = []
    for r in results:
        ids = tuple(sorted([r["id_joueur"], r["partenaire_id"] or ""]))
        if ids in seen:
            continue
        seen.add(ids)
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

    # Stats globales du club
    stats = fetchone("""
        SELECT
          COUNT(*) AS nb_joueurs,
          SUM(CASE WHEN sexe = 'H' THEN 1 ELSE 0 END) AS nb_h,
          SUM(CASE WHEN sexe = 'F' THEN 1 ELSE 0 END) AS nb_f,
          MIN(CASE WHEN sexe = 'H' AND classement IS NOT NULL THEN classement END) AS best_h,
          MIN(CASE WHEN sexe = 'F' AND classement IS NOT NULL THEN classement END) AS best_f,
          ROUND(AVG(classement)) AS avg_rank,
          SUM(CASE WHEN classement IS NOT NULL AND classement <= 100 THEN 1 ELSE 0 END) AS top100,
          SUM(CASE WHEN classement IS NOT NULL AND classement <= 1000 THEN 1 ELSE 0 END) AS top1000
        FROM joueurs
        WHERE club_nom = ?
    """, (nom,)) or {}

    ville_row = fetchone("""
        SELECT ville FROM joueurs WHERE club_nom = ? AND ville IS NOT NULL LIMIT 1
    """, (nom,)) or {}

    # Top joueurs hommes
    top_h = fetchall("""
        SELECT id_fft, nom, prenom, classement, meilleur_classement,
               (SELECT COUNT(*) FROM participations p WHERE p.id_joueur = j.id_fft) AS nb_tournois
        FROM joueurs j
        WHERE club_nom = ? AND sexe = 'H' AND classement IS NOT NULL
        ORDER BY classement ASC LIMIT 8
    """, (nom,))

    # Top joueurs femmes
    top_f = fetchall("""
        SELECT id_fft, nom, prenom, classement, meilleur_classement,
               (SELECT COUNT(*) FROM participations p WHERE p.id_joueur = j.id_fft) AS nb_tournois
        FROM joueurs j
        WHERE club_nom = ? AND sexe = 'F' AND classement IS NOT NULL
        ORDER BY classement ASC LIMIT 8
    """, (nom,))

    def fmt(r):
        return {
            "id": r["id_fft"], "nom": r["nom"] or "", "prenom": r["prenom"] or "",
            "nom_complet": f"{(r.get('prenom') or '').strip()} {r.get('nom') or ''}".strip(),
            "classement": r["classement"], "meilleur_classement": r["meilleur_classement"],
            "nb_tournois": r["nb_tournois"] or 0,
        }

    return jsonify({
        "nom":       nom,
        "ville":     ville_row.get("ville") or "",
        "nb_joueurs": int(stats.get("nb_joueurs") or 0),
        "nb_h":       int(stats.get("nb_h") or 0),
        "nb_f":       int(stats.get("nb_f") or 0),
        "best_h":     stats.get("best_h"),
        "best_f":     stats.get("best_f"),
        "avg_rank":   int(stats.get("avg_rank") or 0) if stats.get("avg_rank") else None,
        "top100":     int(stats.get("top100") or 0),
        "top1000":    int(stats.get("top1000") or 0),
        "top_h":      [fmt(r) for r in top_h],
        "top_f":      [fmt(r) for r in top_f],
    })


@app.get("/api/health")
def route_health():
    return jsonify({
        "status": "ok",
        "graph_loaded": engine._loaded,
        "nb_nodes": len(engine.player_info),
        "nb_links": sum(len(v) for v in engine.graph.values()) // 2,
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_ENV") != "production"
    app.run(debug=debug, host="0.0.0.0", port=port, use_reloader=False)
