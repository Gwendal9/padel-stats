"""
graph_engine.py — Moteur de graphe de partenariats.

Fonctions :
  - Chargement du graphe complet en mémoire (une seule fois au démarrage)
  - BFS : degrés de séparation entre deux joueurs
  - Ego graph : graphe local d'un joueur (jusqu'à N degrés)
"""
import sqlite3
import os
import re
from collections import deque, defaultdict

from db import DB_PATH


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
        """Charge le graphe et les infos joueurs depuis la DB."""
        db = os.path.abspath(DB_PATH)
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)

        # Infos joueurs
        rows = conn.execute("""
            SELECT id_fft, nom, prenom, classement, meilleur_classement,
                   club_nom, sexe, ville, naissance
            FROM joueurs
        """).fetchall()
        for r in rows:
            self.player_info[r[0]] = {
                "id": r[0], "nom": r[1] or "", "prenom": r[2] or "",
                "classement": r[3], "meilleur_classement": r[4],
                "club": r[5] or "", "sexe": r[6] or "",
                "ville": r[7] or "", "naissance": r[8] or "",
            }

        # Liens (dédupliqués, poids = nb tournois joués ensemble)
        rows = conn.execute("""
            SELECT
                MIN(id_joueur, partenaire_id) as a,
                MAX(id_joueur, partenaire_id) as b,
                COUNT(DISTINCT id_tournoi)    as poids
            FROM participations
            WHERE partenaire_id IS NOT NULL AND partenaire_id != ''
              AND id_joueur     IN (SELECT id_fft FROM joueurs)
              AND partenaire_id IN (SELECT id_fft FROM joueurs)
            GROUP BY a, b
        """).fetchall()

        for a, b, poids in rows:
            if a not in self.graph: self.graph[a] = {}
            if b not in self.graph: self.graph[b] = {}
            self.graph[a][b] = poids
            self.graph[b][a] = poids

        conn.close()
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
        """
        Retourne le chemin le plus court entre deux joueurs.

        Résultat :
        {
            "distance": int,
            "path": [
                {"id", "nom", "prenom", "classement", "club",
                 "nb_tournois_avec_suivant": int},   # None pour le dernier
                ...
            ]
        }
        Retourne None si aucun chemin trouvé ou joueur inconnu.
        """
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
                # Optionnellement exclure "Joueur Anonyme" comme intermédiaire
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

        # Aucun chemin direct → réessayer sans exclure les anonymes
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
            "id": pid,
            "nom": info.get("nom", ""),
            "prenom": info.get("prenom", ""),
            "classement": info.get("classement"),
            "club": info.get("club", ""),
            "nb_tournois_avec_suivant": nb_tournois_avec_suivant,
        }

    # ── Ego graph ────────────────────────────────────────────────────────────

    def ego_graph(self, player_id: str, depth: int = 2) -> dict:
        """
        Retourne le graphe local centré sur player_id jusqu'à `depth` degrés.

        Résultat :
        {
            "nodes": [{"id", "label", "classement", "club", "sexe", "degree", "is_center"}],
            "links": [{"source", "target", "weight"}]
        }
        """
        self._ensure_loaded()

        if player_id not in self.graph:
            return {"nodes": [], "links": []}

        # BFS limité à `depth` niveaux
        visited  = {player_id: 0}   # id → distance
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

        # Construire nœuds
        nodes = []
        for pid, degree in visited.items():
            info = self.player_info.get(pid, {})
            label = f"{info.get('prenom', '')} {info.get('nom', '')}".strip() or pid
            nodes.append({
                "id":        pid,
                "label":     label,
                "classement": info.get("classement"),
                "club":      info.get("club", ""),
                "sexe":      info.get("sexe", ""),
                "ville":     info.get("ville", ""),
                "degree":    degree,          # distance au centre (0 = centre)
                "is_center": pid == player_id,
            })

        # Construire liens (seulement entre nœuds dans le graphe)
        links = [
            {"source": a, "target": b, "weight": w}
            for a, b, w in link_set
            if a in visited and b in visited
        ]

        return {"nodes": nodes, "links": links}

    # ── Utilitaire : résoudre des IDs → infos joueurs ─────────────────────

    def resolve(self, ids: list[str]) -> list[dict]:
        """Retourne les infos joueurs pour une liste d'IDs."""
        self._ensure_loaded()
        return [self.player_info[i] for i in ids if i in self.player_info]


# Instance globale partagée (chargée une seule fois au démarrage de l'API)
engine = GraphEngine()
