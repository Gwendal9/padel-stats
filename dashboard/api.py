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

    naissance_rows = fetchall(
        "SELECT sexe, naissance FROM joueurs WHERE naissance IS NOT NULL AND sexe IN ('H','F')"
    )
    pyramid = {"H": [0] * 7, "F": [0] * 7}
    for r in naissance_rows:
        try:
            age = current_year - int(r["naissance"])
        except (ValueError, TypeError):
            continue
        sexe = r.get("sexe")
        if sexe not in pyramid:
            continue
        if age < 18:      b = 0
        elif age <= 25:   b = 1
        elif age <= 35:   b = 2
        elif age <= 45:   b = 3
        elif age <= 55:   b = 4
        elif age <= 65:   b = 5
        else:             b = 6
        pyramid[sexe][b] += 1

    try:
        if USE_POSTGRES:
            month_rows = fetchall("""
                SELECT TO_CHAR(MIN(date_tournoi::date), 'MM/YYYY') AS mois,
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
            def _sort_key(m):
                mm, yyyy = m.split("/")
                return (int(yyyy), int(mm))
            last_12 = sorted(monthly.items(), key=lambda x: _sort_key(x[0]))[-12:]
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

    if club:
        conditions.append("j.club_nom LIKE ?")
        params.append("%" + club + "%")

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
    rows = fetchall("""
        SELECT club_nom, ville, COUNT(*) AS nb_joueurs
        FROM joueurs
        WHERE club_nom IS NOT NULL AND club_nom != ''
          AND ville    IS NOT NULL AND ville    != ''
        GROUP BY club_nom, ville
        ORDER BY nb_joueurs DESC LIMIT ?
    """, (top,))
    return jsonify([{"nom": r["club_nom"], "ville": r["ville"], "nb": r["nb_joueurs"]} for r in rows])


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
