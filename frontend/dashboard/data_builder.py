"""
data_builder.py — Exporte toutes les stats globales en fichiers JSON statiques.
À relancer après chaque nouveau scraping.

Usage : python dashboard/data_builder.py
"""
import json
import os
import re
from datetime import datetime
from collections import defaultdict

from db import get_conn

OUT_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(OUT_DIR, exist_ok=True)


# ── Helpers ──────────────────────────────────────────────────────────────────

def save(filename: str, data):
    path = os.path.join(OUT_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print(f"  ✓ {filename} ({os.path.getsize(path) // 1024} Ko)")


def parse_date(s: str):
    """Convertit DD/MM/YYYY → datetime, ou None."""
    try:
        return datetime.strptime(s.strip(), "%d/%m/%Y")
    except Exception:
        return None


# Ordre des catégories de tournois par niveau croissant
CAT_ORDER = ["P25", "P50", "P100", "P250", "P500", "P1000", "P1500", "P2000", "P3000"]

# Catégories de championnats (hors journées normales)
CHAMP_CATS = [
    "Championnat Départemental",
    "Championnat Régional",
    "Championnat de France",
    "Epreuve par Equipes Padel",
]


def cat_rank(cat: str) -> int:
    """Rang d'une catégorie pour le tri."""
    try:
        return CAT_ORDER.index(cat)
    except ValueError:
        return 99


# ── Builders ─────────────────────────────────────────────────────────────────

def build_stats_globales():
    """KPIs globaux affichés en haut du dashboard."""
    with get_conn() as conn:
        nb_joueurs    = conn.execute("SELECT COUNT(*) FROM joueurs").fetchone()[0]
        nb_hommes     = conn.execute("SELECT COUNT(*) FROM joueurs WHERE sexe='H'").fetchone()[0]
        nb_femmes     = conn.execute("SELECT COUNT(*) FROM joueurs WHERE sexe='F'").fetchone()[0]
        nb_tournois   = conn.execute("SELECT COUNT(*) FROM tournois").fetchone()[0]
        nb_parts      = conn.execute("SELECT COUNT(*) FROM participations").fetchone()[0]

        types_raw     = conn.execute(
            "SELECT type, COUNT(*) FROM participations GROUP BY type"
        ).fetchall()
        types = {r[0]: r[1] for r in types_raw}

        scrape_date = "avril 2026"   # snapshot fixe, à mettre à jour après re-scraping

    save("stats_globales.json", {
        "nb_joueurs": nb_joueurs,
        "nb_hommes": nb_hommes,
        "nb_femmes": nb_femmes,
        "pct_hommes": round(nb_hommes / nb_joueurs * 100, 1),
        "pct_femmes": round(nb_femmes / nb_joueurs * 100, 1),
        "nb_tournois": nb_tournois,
        "nb_participations": nb_parts,
        "moy_tournois_par_joueur": round(nb_parts / nb_joueurs, 1),
        "types_double": {
            "DM": types.get("DM", 0),
            "DD": types.get("DD", 0),
            "DX": types.get("DX", 0),
        },
        "scrape_date": scrape_date,
    })


def build_leaderboard():
    """Top joueurs H et F triés par classement (meilleur = plus petit chiffre)."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT j.id_fft, j.nom, j.prenom, j.classement, j.meilleur_classement,
                   j.club_nom, j.ville, j.naissance, j.sexe,
                   COUNT(DISTINCT p.id_tournoi) as nb_tournois
            FROM joueurs j
            LEFT JOIN participations p ON p.id_joueur = j.id_fft
            WHERE j.classement IS NOT NULL AND j.classement > 0
            GROUP BY j.id_fft
            ORDER BY j.classement ASC
        """).fetchall()

    hommes = []
    femmes = []
    for r in rows:
        entry = {
            "id": r[0], "nom": r[1], "prenom": r[2],
            "classement": r[3], "meilleur_classement": r[4],
            "club": r[5] or "", "ville": r[6] or "",
            "naissance": r[7] or "",
            "nb_tournois": r[9],
        }
        if r[8] == "H":
            hommes.append(entry)
        elif r[8] == "F":
            femmes.append(entry)

    save("leaderboard.json", {"hommes": hommes[:5000], "femmes": femmes[:5000]})


def build_pyramide_ages():
    """Distribution des joueurs par tranche d'âge (10 ans) et sexe."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT naissance, sexe, COUNT(*) as nb
            FROM joueurs
            WHERE naissance IS NOT NULL AND naissance != ''
              AND naissance GLOB '[0-9][0-9][0-9][0-9]'
            GROUP BY naissance, sexe
        """).fetchall()

    tranches = defaultdict(lambda: {"H": 0, "F": 0})
    for naissance, sexe, nb in rows:
        try:
            age = 2026 - int(naissance)
            if age < 0 or age > 90:
                continue
            tranche = f"{(age // 10) * 10}-{(age // 10) * 10 + 9}"
            if sexe in ("H", "F"):
                tranches[tranche][sexe] += nb
        except ValueError:
            pass

    result = sorted(
        [{"tranche": k, "hommes": v["H"], "femmes": v["F"]} for k, v in tranches.items()],
        key=lambda x: int(x["tranche"].split("-")[0])
    )
    save("pyramide_ages.json", result)


def build_distribution_classements():
    """Histogramme des classements (buckets de 5 000)."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT classement FROM joueurs
            WHERE classement IS NOT NULL AND classement > 0 AND classement < 200000
        """).fetchall()

    buckets = defaultdict(int)
    for (cl,) in rows:
        bucket = (cl // 5000) * 5000
        buckets[bucket] += 1

    result = sorted(
        [{"debut": k, "fin": k + 4999, "label": f"{k//1000}k–{(k+4999)//1000}k", "nb": v}
         for k, v in buckets.items()],
        key=lambda x: x["debut"]
    )
    save("distribution_classements.json", result)


def build_top_clubs():
    """Top 30 clubs par nombre de joueurs ayant participé à au moins 1 tournoi."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT j.club_nom, j.sexe, COUNT(DISTINCT j.id_fft) as nb_joueurs,
                   ROUND(AVG(j.classement), 0) as classement_moyen
            FROM joueurs j
            INNER JOIN participations p ON p.id_joueur = j.id_fft
            WHERE j.club_nom IS NOT NULL AND j.club_nom != ''
            GROUP BY j.club_nom, j.sexe
        """).fetchall()

    clubs = defaultdict(lambda: {"H": 0, "F": 0, "classements": []})
    for club, sexe, nb, cl_moy in rows:
        if sexe in ("H", "F"):
            clubs[club][sexe] += nb
        if cl_moy:
            clubs[club]["classements"].append(cl_moy)

    result = []
    for club, data in clubs.items():
        total = data["H"] + data["F"]
        cl_moy = round(sum(data["classements"]) / len(data["classements"])) if data["classements"] else None
        result.append({
            "club": club,
            "nb_total": total,
            "nb_hommes": data["H"],
            "nb_femmes": data["F"],
            "classement_moyen": cl_moy,
        })

    result.sort(key=lambda x: x["nb_total"], reverse=True)
    save("top_clubs.json", result[:50])


def build_top_villes():
    """Top 50 villes par nombre de joueurs."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT ville, COUNT(*) as nb
            FROM joueurs
            WHERE ville IS NOT NULL AND ville != ''
            GROUP BY ville
            ORDER BY nb DESC
            LIMIT 50
        """).fetchall()

    save("top_villes.json", [{"ville": r[0], "nb": r[1]} for r in rows])


def build_tournois_distribution():
    """Distribution des tournois et participations par catégorie."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT t.categorie,
                   COUNT(DISTINCT t.id_tournoi) as nb_tournois,
                   COUNT(p.id)                  as nb_participations
            FROM tournois t
            LEFT JOIN participations p ON p.id_tournoi = t.id_tournoi
            GROUP BY t.categorie
        """).fetchall()

    # Séparer journées classiques et championnats
    classiques = []
    championnats = []
    for cat, nb_t, nb_p in rows:
        entry = {"categorie": cat, "nb_tournois": nb_t, "nb_participations": nb_p}
        is_champ = any(c in cat for c in CHAMP_CATS)
        if is_champ:
            championnats.append(entry)
        else:
            classiques.append(entry)

    classiques.sort(key=lambda x: cat_rank(x["categorie"]))
    championnats.sort(key=lambda x: x["nb_tournois"], reverse=True)

    save("tournois_distribution.json", {
        "classiques": classiques,
        "championnats": championnats,
    })


def build_bareme_points():
    """Points médians par position (1–16) et catégorie de tournoi classique."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT t.categorie, p.position, p.points
            FROM participations p
            JOIN tournois t ON p.id_tournoi = t.id_tournoi
            WHERE p.position != '' AND p.points != ''
              AND CAST(p.position AS INT) BETWEEN 1 AND 16
              AND t.categorie IN ('P25','P50','P100','P250','P500','P1000','P1500','P2000')
        """).fetchall()

    from statistics import median
    data = defaultdict(lambda: defaultdict(list))
    for cat, pos, pts in rows:
        try:
            data[cat][int(pos)].append(int(pts))
        except (ValueError, TypeError):
            pass

    result = {}
    for cat in CAT_ORDER:
        if cat not in data:
            continue
        result[cat] = {}
        for pos in sorted(data[cat].keys()):
            vals = data[cat][pos]
            result[cat][pos] = {
                "median": int(median(vals)),
                "max": max(vals),
                "nb": len(vals),
            }

    save("bareme_points.json", result)


def build_saisonnalite():
    """Nombre de tournois et participations par mois (sur toute la base)."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT p.date_tournoi, t.categorie, COUNT(*) as nb
            FROM participations p
            JOIN tournois t ON p.id_tournoi = t.id_tournoi
            WHERE p.date_tournoi IS NOT NULL AND p.date_tournoi != ''
            GROUP BY p.date_tournoi, t.categorie
        """).fetchall()

    mois_data = defaultdict(lambda: {"participations": 0, "nb_cat": defaultdict(int)})
    for date_str, cat, nb in rows:
        dt = parse_date(date_str)
        if not dt:
            continue
        key = f"{dt.year}-{dt.month:02d}"
        mois_data[key]["participations"] += nb
        # Regrouper les catégories principales
        cat_simple = cat if cat in CAT_ORDER else "Autres"
        mois_data[key]["nb_cat"][cat_simple] += nb

    result = sorted(
        [{"mois": k,
          "label": datetime.strptime(k, "%Y-%m").strftime("%b %Y"),
          "participations": v["participations"],
          "par_categorie": dict(v["nb_cat"])}
         for k, v in mois_data.items()],
        key=lambda x: x["mois"]
    )
    save("saisonnalite.json", result)


def build_hall_of_fame():
    """Records all-time : le plus de tournois, le plus de victoires, etc."""
    with get_conn() as conn:
        # Plus de tournois joués
        plus_tournois = conn.execute("""
            SELECT j.id_fft, j.nom, j.prenom, j.classement, j.club_nom,
                   COUNT(DISTINCT p.id_tournoi) as nb
            FROM participations p JOIN joueurs j ON p.id_joueur = j.id_fft
            GROUP BY p.id_joueur ORDER BY nb DESC LIMIT 5
        """).fetchall()

        # Plus de victoires (position = 1)
        plus_victoires = conn.execute("""
            SELECT j.id_fft, j.nom, j.prenom, j.classement, j.club_nom,
                   COUNT(*) as nb
            FROM participations p JOIN joueurs j ON p.id_joueur = j.id_fft
            WHERE p.position = '1'
            GROUP BY p.id_joueur ORDER BY nb DESC LIMIT 5
        """).fetchall()

        # Plus de partenaires différents
        plus_partenaires = conn.execute("""
            SELECT j.id_fft, j.nom, j.prenom, j.classement, j.club_nom,
                   COUNT(DISTINCT p.partenaire_id) as nb
            FROM participations p JOIN joueurs j ON p.id_joueur = j.id_fft
            WHERE p.partenaire_id IS NOT NULL AND p.partenaire_id != ''
              AND p.partenaire_nom != 'Joueur Anonyme'
            GROUP BY p.id_joueur ORDER BY nb DESC LIMIT 5
        """).fetchall()

        # Plus grand écart classement actuel / meilleur (le plus en déclin)
        plus_declin = conn.execute("""
            SELECT id_fft, nom, prenom, classement, meilleur_classement, club_nom,
                   (classement - meilleur_classement) as ecart
            FROM joueurs
            WHERE classement IS NOT NULL AND meilleur_classement IS NOT NULL
              AND classement > 0 AND meilleur_classement > 0
            ORDER BY ecart DESC LIMIT 5
        """).fetchall()

        # Plus jeune dans le top 1000
        plus_jeune_top = conn.execute("""
            SELECT id_fft, nom, prenom, classement, naissance, club_nom
            FROM joueurs
            WHERE classement IS NOT NULL AND classement <= 1000
              AND naissance GLOB '[0-9][0-9][0-9][0-9]'
            ORDER BY CAST(naissance AS INT) DESC LIMIT 5
        """).fetchall()

    def fmt(rows, keys):
        return [{k: r[i] for i, k in enumerate(keys)} for r in rows]

    save("hall_of_fame.json", {
        "plus_tournois":    fmt(plus_tournois,    ["id","nom","prenom","classement","club","nb"]),
        "plus_victoires":   fmt(plus_victoires,   ["id","nom","prenom","classement","club","nb"]),
        "plus_partenaires": fmt(plus_partenaires, ["id","nom","prenom","classement","club","nb"]),
        "plus_declin":      fmt(plus_declin,       ["id","nom","prenom","classement","meilleur_classement","club","ecart"]),
        "plus_jeune_top":   fmt(plus_jeune_top,   ["id","nom","prenom","classement","naissance","club"]),
    })


def build_top_progressions():
    """Joueurs avec le meilleur ratio classement actuel / meilleur all-time."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT j.id_fft, j.nom, j.prenom, j.classement, j.meilleur_classement,
                   j.club_nom, j.ville, j.sexe,
                   (j.meilleur_classement - j.classement) as gain,
                   COUNT(DISTINCT p.id_tournoi) as nb_tournois
            FROM joueurs j
            LEFT JOIN participations p ON p.id_joueur = j.id_fft
            WHERE j.classement IS NOT NULL AND j.meilleur_classement IS NOT NULL
              AND j.classement > 0 AND j.meilleur_classement > 0
              AND j.classement = j.meilleur_classement  -- actuellement à leur meilleur
            GROUP BY j.id_fft
            HAVING nb_tournois >= 3
            ORDER BY j.classement ASC
            LIMIT 100
        """).fetchall()

    result = [{
        "id": r[0], "nom": r[1], "prenom": r[2],
        "classement": r[3], "meilleur_classement": r[4],
        "club": r[5] or "", "ville": r[6] or "", "sexe": r[7],
        "nb_tournois": r[9],
    } for r in rows]

    save("top_progressions.json", result)


# ── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Construction des stats globales...\n")
    steps = [
        ("Stats globales",            build_stats_globales),
        ("Leaderboard",               build_leaderboard),
        ("Pyramide des âges",         build_pyramide_ages),
        ("Distribution classements",  build_distribution_classements),
        ("Top clubs",                 build_top_clubs),
        ("Top villes",                build_top_villes),
        ("Distribution tournois",     build_tournois_distribution),
        ("Barème des points",         build_bareme_points),
        ("Saisonnalité",              build_saisonnalite),
        ("Hall of Fame",              build_hall_of_fame),
        ("Top progressions",          build_top_progressions),
    ]
    for label, fn in steps:
        print(f"→ {label}...")
        try:
            fn()
        except Exception as e:
            print(f"  ✗ ERREUR : {e}")

    print("\nTerminé. Fichiers dans dashboard/data/")
