"""
graph_engine.py — Moteur de graphe de partenariats.

Fonctions :
  - Chargement du graphe complet en mémoire (une seule fois au démarrage)
  - BFS : degrés de séparation entre deux joueurs
  - Ego graph : graphe local d'un joueur (jusqu'à N degrés)
"""
import os
from collections import deque, defaultdict

from db import fetchall, USE_POSTGRES


class GraphEngine:
    """
    Charge et maintient en mémoire le graphe de partenariats.
    Un lien = deux joueurs ayant disputé au moins 1 tournoi ensemble.
    """

    def __init__(self):
        self.graph: dict[str, dict[str, int]] = {}   # id → {voisin: nb_tournois}
        self.player_info: dict[str, dict] = {}        # id → {nom, prenom, classement, club, sexe, ville}
        self._loaded = False

    # ── Chargement ───────────────────────────────────────────────────────────

    def load(self):
        """Charge le graphe et les infos joueurs depuis la DB (SQLite ou PostgreSQL)."""

        # Infos joueurs
        rows = fetchall("""
            SELECT id_fft, nom, prenom, classement, meilleur_classement,
                   club_nom, sexe, ville, naissance
            FROM joueurs
        """)
        for r in rows:
            self.player_info[r["id_fft"]] = {
                "id":                  r["id_fft"],
                "nom":                 r["nom"] or "",
                "prenom":              r["prenom"] or "",
                "classement":          r["classement"],
                "meilleur_classement": r["meilleur_classement"],
                "club":                r["club_nom"] or "",
                "sexe":                r["sexe"] or "",
                "ville":               r["ville"] or "",
                "naissance":           r["naissance"] or "",
            }

        # Liens — syntaxe MIN/MAX (SQLite) vs LEAST/GREATEST (PostgreSQL)
        if USE_POSTGRES:
            links_query = """
                SELECT
                    LEAST(id_joueur, partenaire_id)    AS a,
                    GREATEST(id_joueur, partenaire_id) AS b,
                    COUNT(DISTINCT id_tournoi)          AS poids
                FROM participations
                WHERE partenaire_id IS NOT NULL AND partenaire_id != ''
                  AND id_joueur     IN (SELECT id_fft FROM joueurs)
                  AND partenaire_id IN (SELECT id_fft FROM joueurs)
                GROUP BY a, b
            """
        else:
            links_query = """
                SELECT
                    MIN(id_joueur, partenaire_id) AS a,
                    MAX(id_joueur, partenaire_id) AS b,
                    COUNT(DISTINCT id_tournoi)    AS poids
                FROM participations
                WHERE partenaire_id IS NOT NULL AND partenaire_id != ''
                  AND id_joueur     IN (SELECT id_fft FROM joueurs)
                  AND partenaire_id IN (SELECT id_fft FROM joueurs)
                GROUP BY a, b
            """

        rows = fetchall(links_query)
        for r in rows:
            a, b, poids = r["a"], r["b"], r["poids"]
            if a not in self.graph: self.graph[a] = {}
            if b not in self.graph: self.graph[b] = {}
            self.graph[a][b] = poids
            self.graph[b][a] = poids

        self._loaded = True
        print(f"[GraphEngine] {len(self.player_info):,} joueurs · {len(rows):,} liens chargés")

    def _ensure_loaded(self):
        if not self._loaded:
            self.load()

    # ── BFS degrés de séparation ─────────────────────────────────────────────

    def shortest_path(
        self,
        src_id: str,
        tgt_id: str,
        exclude_anonymous: bool = True,
    ) -> dict | None:
        self._ensure_loaded()

        if src_id not in self.graph or tgt_id not in self.graph:
            return None
        if src_id == tgt_id:
            return {"distance": 0, "path": [self._node_info(src_id, None)]}

        queue   = deque([(src_id, [src_id])])
        visited = {src_id}

        while queue:
            node, path = queue.popleft()
            for neighbor in self.graph.get(node, {}):
                if exclude_anonymous and neighbor != tgt_id:
                    info = self.player_info.get(neighbor, {})
                    if info.get("nom", "").upper() == "ANONYME" or \
                       info.get("prenom", "").upper() == "JOUEUR":
                        continue
                if neighbor == tgt_id:
                    full_path = path + [neighbor]
                    return self._format_path(full_path)
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, path + [neighbor]))

        if exclude_anonymous:
            return self.shortest_path(src_id, tgt_id, exclude_anonymous=False)

        return None

    def _format_path(self, path: list[str]) -> dict:
        result = []
        for i, pid in enumerate(path):
            next_pid = path[i + 1] if i + 1 < len(path) else None
            nb = self.graph.get(pid, {}).get(next_pid) if next_pid else None
            result.append(self._node_info(pid, nb))
        return {"distance": len(path) - 1, "path": result}

    def _node_info(self, pid: str, nb_tournois_avec_suivant) -> dict:
        info = self.player_info.get(pid, {})
        return {
            "id":                        pid,
            "nom":                       info.get("nom", ""),
            "prenom":                    info.get("prenom", ""),
            "classement":                info.get("classement"),
            "club":                      info.get("club", ""),
            "nb_tournois_avec_suivant":  nb_tournois_avec_suivant,
        }

    # ── Ego graph ────────────────────────────────────────────────────────────

    def ego_graph(self, player_id: str, depth: int = 2) -> dict:
        self._ensure_loaded()

        if player_id not in self.graph:
            return {"nodes": [], "links": []}

        visited  = {player_id: 0}
        queue    = deque([(player_id, 0)])
        link_set = set()

        while queue:
            node, dist = queue.popleft()
            if dist >= depth:
                continue
            for neighbor, poids in self.graph.get(node, {}).items():
                link_key = (min(node, neighbor), max(node, neighbor))
                link_set.add((link_key[0], link_key[1], poids))
                if neighbor not in visited:
                    visited[neighbor] = dist + 1
                    queue.append((neighbor, dist + 1))

        nodes = []
        for pid, degree in visited.items():
            info = self.player_info.get(pid, {})
            label = f"{info.get('prenom', '')} {info.get('nom', '')}".strip() or pid
            nodes.append({
                "id":         pid,
                "label":      label,
                "classement": info.get("classement"),
                "club":       info.get("club", ""),
                "sexe":       info.get("sexe", ""),
                "ville":      info.get("ville", ""),
                "degree":     degree,
                "is_center":  pid == player_id,
            })

        links = [
            {"source": a, "target": b, "weight": w}
            for a, b, w in link_set
            if a in visited and b in visited
        ]

        return {"nodes": nodes, "links": links}

    # ── Utilitaire ────────────────────────────────────────────────────────────

    def resolve(self, ids: list[str]) -> list[dict]:
        self._ensure_loaded()
        return [self.player_info[i] for i in ids if i in self.player_info]


# Instance globale partagée
engine = GraphEngine()
