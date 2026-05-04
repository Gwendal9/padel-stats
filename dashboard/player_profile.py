"""
player_profile.py — Requêtes pour le profil complet d'un joueur.

Fonctions exportées :
  search_players(q)          → liste de joueurs correspondant à la recherche
  get_player_profile(id)     → profil complet (stats, trophy shelf, partenaires, championnats)
"""
import re
from collections import defaultdict

from db import fetchall, fetchone, fetchval

# Catégories "journées classiques" ordonnées par niveau
CAT_CLASSIQUES = ["P25", "P50", "P100", "P250", "P500", "P1000", "P1500", "P2000", "P3000"]

# Mots-clés pour détecter les catégories "championnat"
CHAMP_KEYWORDS = ["Championnat", "Epreuve par Equipes"]


def _is_champ(categorie: str) -> bool:
    return any(k in categorie for k in CHAMP_KEYWORDS)


# ── Recherche joueur ──────────────────────────────────────────────────────────

def search_players(q: str, limit: int = 20) -> list[dict]:
    """
    Recherche par nom / prénom (insensible à la casse).
    Retourne jusqu'à `limit` résultats triés par classement.
    """
    q = q.strip()
    if not q:
        return []

    pattern = f"%{q}%"
    rows = fetchall(
        """
        SELECT id_fft, nom, prenom, classement, meilleur_classement,
               club_nom, ville, sexe, naissance
        FROM joueurs
        WHERE (nom LIKE ? OR prenom LIKE ? OR (nom || ' ' || prenom) LIKE ? OR (prenom || ' ' || nom) LIKE ?)
          AND classement IS NOT NULL
        ORDER BY classement ASC
        LIMIT ?
        """,
        (pattern, pattern, pattern, pattern, limit),
    )
    return [_fmt_joueur(r) for r in rows]


def _fmt_joueur(r: dict) -> dict:
    age = None
    if r.get("naissance") and str(r["naissance"]).isdigit():
        age = 2026 - int(r["naissance"])
    return {
        "id":                  r["id_fft"],
        "nom":                 r["nom"] or "",
        "prenom":              r["prenom"] or "",
        "nom_complet":         f"{r.get('prenom','')} {r.get('nom','')}".strip(),
        "classement":          r["classement"],
        "meilleur_classement": r["meilleur_classement"],
        "club":                r["club_nom"] or "",
        "ville":               r["ville"] or "",
        "sexe":                r["sexe"] or "",
        "age":                 age,
    }


# ── Profil complet ────────────────────────────────────────────────────────────

def get_player_profile(player_id: str) -> dict | None:
    """
    Construit le profil complet d'un joueur :
      - Infos de base
      - KPIs (nb tournois, points totaux, position moyenne)
      - Trophy shelf (top 5 par catégorie classique)
      - Top partenaires
      - Distribution des positions
      - Championnats (meilleurs résultats)
    """
    # ── Infos de base ─────────────────────────────────────────────────────
    base = fetchone(
        """
        SELECT id_fft, nom, prenom, classement, meilleur_classement,
               club_nom, ville, sexe, naissance
        FROM joueurs WHERE id_fft = ?
        """,
        (player_id,),
    )
    if not base:
        return None

    joueur = _fmt_joueur(base)

    # ── Toutes ses participations ─────────────────────────────────────────
    parts = fetchall(
        """
        SELECT p.position, p.points, p.partenaire_id, p.partenaire_nom,
               p.date_tournoi, p.type, t.categorie, t.nom as tournoi_nom, p.id_tournoi
        FROM participations p
        JOIN tournois t ON p.id_tournoi = t.id_tournoi
        WHERE p.id_joueur = ?
        ORDER BY p.date_tournoi DESC
        """,
        (player_id,),
    )

    # Séparer classiques et championnats
    parts_classiques   = [p for p in parts if not _is_champ(p["categorie"])]
    parts_championnats = [p for p in parts if _is_champ(p["categorie"])]

    # ── KPIs globaux ──────────────────────────────────────────────────────
    nb_tournois = len(parts)
    points_vals = [int(p["points"]) for p in parts if p["points"] and str(p["points"]).isdigit()]
    points_total = sum(points_vals)

    pos_vals = [int(p["position"]) for p in parts if p["position"] and str(p["position"]).isdigit()]
    pos_moyenne = round(sum(pos_vals) / len(pos_vals), 1) if pos_vals else None

    nb_victoires = sum(1 for p in parts_classiques if p["position"] == "1")

    joueur.update({
        "nb_tournois":   nb_tournois,
        "points_total":  points_total,
        "pos_moyenne":   pos_moyenne,
        "nb_victoires":  nb_victoires,
    })

    # ── Trophy shelf — top 5 par catégorie classique ──────────────────────
    shelf = {}
    for part in parts_classiques:
        cat = part["categorie"]
        if cat not in CAT_CLASSIQUES:
            continue
        try:
            pos = int(part["position"])
        except (ValueError, TypeError):
            continue
        if pos > 5:
            continue
        if cat not in shelf:
            shelf[cat] = {str(i): 0 for i in range(1, 6)}
        shelf[cat][str(pos)] += 1

    # Trier les catégories par niveau
    trophy_shelf = {
        cat: shelf[cat]
        for cat in CAT_CLASSIQUES
        if cat in shelf
    }

    # ── Distribution des positions (classiques) ───────────────────────────
    positions_dist = defaultdict(int)
    for p in parts_classiques:
        if p["position"] and str(p["position"]).isdigit():
            positions_dist[int(p["position"])] += 1
    positions_dist = dict(sorted(positions_dist.items()))

    # ── Top partenaires ───────────────────────────────────────────────────
    partners_raw = defaultdict(lambda: {"nb": 0, "victoires": 0, "positions": []})
    for p in parts_classiques:
        pid = p["partenaire_id"]
        if not pid or pid == "":
            continue
        nom = p["partenaire_nom"] or "?"
        partners_raw[(pid, nom)]["nb"] += 1
        if p["position"] == "1":
            partners_raw[(pid, nom)]["victoires"] += 1
        try:
            partners_raw[(pid, nom)]["positions"].append(int(p["position"]))
        except (ValueError, TypeError):
            pass

    top_partners = []
    for (pid, nom), data in sorted(partners_raw.items(), key=lambda x: -x[1]["nb"]):
        pos_list = data["positions"]
        pos_moy = round(sum(pos_list) / len(pos_list), 1) if pos_list else None
        taux_vic = round(data["victoires"] / data["nb"] * 100) if data["nb"] > 0 else 0

        # Récupérer infos du partenaire si en base
        partner_info = fetchone(
            "SELECT classement, club_nom FROM joueurs WHERE id_fft = ?", (pid,)
        ) or {}

        top_partners.append({
            "id":           pid,
            "nom_complet":  nom,
            "nb_tournois":  data["nb"],
            "nb_victoires": data["victoires"],
            "taux_victoire": taux_vic,
            "pos_moyenne":  pos_moy,
            "classement":   partner_info.get("classement"),
            "club":         partner_info.get("club_nom", ""),
        })

    top_partners = top_partners[:10]

    # ── Championnats — meilleurs résultats ────────────────────────────────
    champ_best = defaultdict(lambda: None)
    for p in parts_championnats:
        cat = p["categorie"]
        try:
            pos = int(p["position"])
        except (ValueError, TypeError):
            continue
        if champ_best[cat] is None or pos < champ_best[cat]["position"]:
            # Extraire le tour (R1/R2/R3) depuis le nom du tournoi si présent
            tour_match = re.search(r"\bR(\d)\b", p["tournoi_nom"] or "", re.IGNORECASE)
            champ_best[cat] = {
                "categorie":   cat,
                "position":    pos,
                "tournoi_nom": p["tournoi_nom"] or "",
                "date":        p["date_tournoi"] or "",
                "partenaire":  p["partenaire_nom"] or "",
                "points":      p["points"],
                "tour":        f"R{tour_match.group(1)}" if tour_match else None,
            }

    championnats = sorted(
        champ_best.values(),
        key=lambda x: x["position"]
    )

    # ── Historique récent (10 derniers tournois) ──────────────────────────
    historique = []
    for p in parts[:10]:
        historique.append({
            "id_tournoi":   p["id_tournoi"],
            "tournoi_nom":  p["tournoi_nom"] or "",
            "categorie":    p["categorie"],
            "date":         p["date_tournoi"] or "",
            "position":     p["position"],
            "points":       p["points"],
            "partenaire":   p["partenaire_nom"] or "",
            "type":         p["type"] or "",
        })

    # ── Parcours complet (chronologique, classiques) ─────────────────────
    parcours = [
        {
            "date":        p["date_tournoi"] or "",
            "position":    p["position"],
            "categorie":   p["categorie"],
            "tournoi_nom": p["tournoi_nom"] or "",
            "partenaire":  p["partenaire_nom"] or "",
            "id_tournoi":  p["id_tournoi"],
            "points":      p["points"],
        }
        for p in reversed(parts_classiques)
    ]

    # ── Dates d'activité pour heatmap ────────────────────────────────────
    date_activities = sorted(set(
        p["date_tournoi"] for p in parts if p["date_tournoi"]
    ))

    return {
        **joueur,
        "trophy_shelf":      trophy_shelf,
        "positions_dist":    positions_dist,
        "top_partners":      top_partners,
        "championnats":      championnats,
        "historique_recent": historique,
        "parcours":          parcours,
        "date_activities":   date_activities,
        "nb_partenaires":    len(partners_raw),
    }
