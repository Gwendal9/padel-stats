"""
api.py — API Flask pour le dashboard Padel Stats.

Routes :
  GET /api/search?q=ROLLAND          → recherche joueurs
  GET /api/player/<id>               → profil complet joueur
  GET /api/suggest/<id>              → suggestions de partenaires
  GET /api/path/<src_id>/<tgt_id>    → degrés de séparation (BFS)
  GET /api/ego/<id>?depth=2          → graphe local ego

Lancement :
  cd dashboard && python api.py
  → http://localhost:5000

Le graphe est chargé en mémoire au démarrage (~3–5s).
Toutes les requêtes BFS répondent ensuite en < 20ms.
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
    """
    Degrés de séparation entre deux joueurs (BFS).
    Réponse : { distance: int, path: [...] }
    """
    result = engine.shortest_path(src_id, tgt_id)
    if result is None:
        return jsonify({"error": "Aucun chemin trouvé entre ces deux joueurs"}), 404
    return jsonify(result)


@app.get("/api/ego/<player_id>")
def route_ego(player_id: str):
    """
    Graphe local centré sur un joueur (ego graph).
    ?depth=2 (défaut) → partenaires des partenaires
    """
    depth = min(int(request.args.get("depth", 2)), 3)
    graph_data = engine.ego_graph(player_id, depth=depth)
    if not graph_data["nodes"]:
        return jsonify({"error": "Joueur introuvable ou sans partenaires"}), 404
    return jsonify(graph_data)


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
    print("Chargement du graphe en mémoire (peut prendre quelques secondes)...")
    engine.load()
    print(f"Graphe prêt : {len(engine.player_info):,} joueurs · {sum(len(v) for v in engine.graph.values())//2:,} liens")
    print("\nServeur démarré sur http://localhost:5000\n")
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_ENV") != "production"
    app.run(debug=debug, host="0.0.0.0", port=port, use_reloader=False)
