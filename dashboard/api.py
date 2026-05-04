"""
api.py — API Flask pour le dashboard Padel Stats.

Routes :
  GET /api/search?q=ROLLAND          → recherche joueurs
  GET /api/player/<id>               → profil complet joueur
  GET /api/suggest/<id>              → suggestions de partenaires
  GET /api/path/<src_id>/<tgt_id>    → degrés de séparation (BFS)
  GET /api/ego/<id>?depth=2          → graphe local ego
  GET /api/stats                     → stats globales pour dashboard
  GET /api/clubs?top=100             → top clubs par nb joueurs

Lancement :
  cd dashboard && python api.py
  → http://localhost:5000
"""
import os
import sys

# Permettre les imports depuis le dossier dashboard/
sys.path.insert(0, os.path.dirname(__file__))

from flask import Flask, jsonify, request, send_file
from flask_cors import CORS

from graph_engine import engine
from player_profile import search_players, get_player_profile
from suggester import suggest_partners

app = Flask(__name__)
CORS(app)   # Autorise les requêtes cross-origin depuis le HTML statique


# ── Serve frontend ───────────────────────────────────────────────────────────

@app.route("/")
def index():
    """Sert le fichier HTML du dashboard."""
    html_path = os.path.join(os.path.dirname(__file__), "..", "dashboard_mockup.html")
    return send_file(os.path.abspath(html_path))


# ── Graphe chargé uniquement à la demande (path + ego) ───────────────────────
# Les routes search / player / suggest n'en ont pas besoin → démarrage immédiat


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/api/search")
def route_search():
    """Recherche de joueurs par nom/prénom."""
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify([])
    limit = min(int(request.args.get("limit", 20)), 50)
    results = search_players(q, limit=limit)
    return jsonify(results)


@app.get("/api/player/<player_id>")
def route_player(player_id: str):
    """Profil complet d'un joueur."""
    profile = get_player_profile(player_id)
    if not profile:
        return jsonify({"error": "Joueur introuvable"}), 404
    return jsonify(profile)


@app.get("/api/suggest/<player_id>")
def route_suggest(player_id: str):
    """Suggestions de partenaires pour un joueur."""
    n = min(int(request.args.get("n", 10)), 30)
    suggestions = suggest_partners(player_id, n=n)
    return jsonify(suggestions)


@app.get("/api/path/<src_id>/<tgt_id>")
def route_path(src_id: str, tgt_id: str):
    """Degrés de séparation entre deux joueurs (BFS)."""
    result = engine.shortest_path(src_id, tgt_id)
    if result is None:
        return jsonify({"error": "Aucun chemin trouvé entre ces deux joueurs"}), 404
    return jsonify(result)


@app.get("/api/ego/<player_id>")
def route_ego(player_id: str):
    """Graphe local centré sur un joueur (ego graph)."""
    depth = min(int(request.args.get("depth", 2)), 3)
    graph_data = engine.ego_graph(player_id, depth=depth)
    if not graph_data["nodes"]:
        return jsonify({"error": "Joueur introuvable ou sans partenaires"}), 404
    return jsonify(graph_data)


@app.get("/api/stats")
def route_stats():
    """Stats globales pour les charts du dashboard."""
    from db import fetchall, fetchone
    import datetime
    from collections import defaultdict

    current_year = datetime.date.today().year

    # 1. Distribution classements
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

    # 2. Pyramide âges — calcul en Python pour compatibilité SQLite/PostgreSQL
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
        if age < 18:
            b = 0
        elif age <= 25:
            b = 1
        elif age <= 35:
            b = 2
        elif age <= 45:
            b = 3
        elif age <= 55:
            b = 4
        elif age <= 65:
            b = 5
        else:
            b = 6
        pyramid[sexe][b] += 1

    # 3. Activité mensuelle — calcul en Python pour compatibilité des formats de date
    date_rows = fetchall(
        "SELECT DISTINCT id_tournoi, date_tournoi FROM participations WHERE date_tournoi IS NOT NULL"
    )
    monthly = defaultdict(int)
    for r in date_rows:
        d = r.get("date_tournoi") or ""
        try:
            if len(d) == 10 and d[2] == "/":   # DD/MM/YYYY
                mois = d[3:5] + "/" + d[6:]
            elif len(d) == 10 and d[4] == "-":  # YYYY-MM-DD
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

    # 4. Top villes (proxy régions)
    villes = fetchall("""
        SELECT UPPER(TRIM(ville)) AS ville, COUNT(*) AS nb
        FROM joueurs WHERE ville IS NOT NULL AND ville != ''
        GROUP BY UPPER(TRIM(ville))
        ORDER BY nb DESC LIMIT 10
    """)

    # 5. Distribution taille tournois (nb paires)
    tourn_sizes = fetchall(
        "SELECT id_tournoi, COUNT(*) AS nb_parts FROM participations GROUP BY id_tournoi"
    )
    tdist = [0, 0, 0, 0, 0, 0]  # ≤8, 9-16, 17-32, 33-64, 65-128, 128+
    for r in tourn_sizes:
        pairs = (r.get("nb_parts") or 0) // 2
        if pairs <= 8:
            tdist[0] += 1
        elif pairs <= 16:
            tdist[1] += 1
        elif pairs <= 32:
            tdist[2] += 1
        elif pairs <= 64:
            tdist[3] += 1
        elif pairs <= 128:
            tdist[4] += 1
        else:
            tdist[5] += 1

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


@app.get("/api/clubs")
def route_clubs():
    """Top clubs par nombre de joueurs, avec ville majoritaire."""
    from db import fetchall
    top = min(int(request.args.get("top", 100)), 500)
    rows = fetchall("""
        SELECT club_nom,
               ville,
               COUNT(*) AS nb_joueurs
        FROM joueurs
        WHERE club_nom IS NOT NULL AND club_nom != ''
          AND ville    IS NOT NULL AND ville    != ''
        GROUP BY club_nom, ville
        ORDER BY nb_joueurs DESC
        LIMIT ?
    """, (top,))
    return jsonify([{"nom": r["club_nom"], "ville": r["ville"], "nb": r["nb_joueurs"]} for r in rows])


@app.get("/api/health")
def route_health():
    """Check de santé — vérifie que le graphe est chargé."""
    return jsonify({
        "status": "ok",
        "graph_loaded": engine._loaded,
        "nb_nodes": len(engine.player_info),
        "nb_links": sum(len(v) for v in engine.graph.values()) // 2,
    })


# ── Point d'entrée ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Démarrage de l'API Padel Stats...")
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_ENV") != "production"
    app.run(debug=debug, host="0.0.0.0", port=port, use_reloader=False)
