"""
player_profile.py — Requêtes pour le profil complet d'un joueur.

Fonctions exportées :
  search_players(q)          → liste de joueurs correspondant à la recherche
  get_player_profile(id)     → profil complet (stats, trophy shelf, partenaires, championnats)
"""
import re
import datetime
from collections import defaultdict

from db import fetchall, fetchone, fetchval, USE_POSTGRES

# Catégories "journées classiques" ordonnées par niveau
CAT_CLASSIQUES = ["P25", "P50", "P100", "P250", "P500", "P1000", "P1500", "P2000", "P3000"]

# Mots-clés pour détecter les catégories "championnat"
CHAMP_KEYWORDS = ["Championnat", "Epreuve par Equipes"]


def _is_champ(categorie: str) -> bool:
    return any(k in categorie for k in CHAMP_KEYWORDS)


# ── Recherche joueur ──────────────────────────────────────────────────────────

def search_players(q: str, limit: int = 20, sexe: str | None = None) -> list[dict]:
    """
    Recherche par nom / prénom (insensible à la casse).
    Retourne jusqu'à `limit` résultats triés par classement.
    sexe='H' ou 'F' pour filtrer par sexe (optionnel).
    """
    q = q.strip()
    if not q:
        return []

    # Prefix match (q%) : utilise l'index B-tree sur nom/prenom → instantané.
    # Fallback substring (%q%) sur le nom complet pour les recherches "Prénom NOM".
    prefix  = f"{q}%"
    substr  = f"%{q}%"
    sexe_clause = "AND sexe = ?" if sexe in ("H", "F") else ""
    params = (prefix, prefix, substr, substr)
    if sexe in ("H", "F"):
        params = params + (sexe,)
    params = params + (limit,)
    rows = fetchall(
        f"""
        SELECT id_fft, nom, prenom, classement, meilleur_classement,
               variation_classement, classement_date,
               club_nom, ville, sexe, naissance
        FROM joueurs
        WHERE (nom LIKE ? OR prenom LIKE ? OR (nom || ' ' || prenom) LIKE ? OR (prenom || ' ' || nom) LIKE ?)
          AND classement IS NOT NULL
          {sexe_clause}
        ORDER BY classement ASC
        LIMIT ?
        """,
        params,
    )
    return [_fmt_joueur(r) for r in rows]


def _fmt_joueur(r: dict) -> dict:
    naissance_annee = None
    if r.get("naissance") and str(r["naissance"]).isdigit():
        naissance_annee = int(r["naissance"])
    return {
        "id":                    r["id_fft"],
        "nom":                   r["nom"] or "",
        "prenom":                r["prenom"] or "",
        "nom_complet":           f"{r.get('prenom','')} {r.get('nom','')}".strip(),
        "classement":            r["classement"],
        "meilleur_classement":   r["meilleur_classement"],
        "variation_classement":  r.get("variation_classement"),  # delta mensuel FFT
        "classement_date":       r.get("classement_date"),       # mois YYYY-MM du snapshot
        "club":                  r["club_nom"] or "",
        "ville":                 r["ville"] or "",
        "sexe":                  r["sexe"] or "",
        # On ne stocke que l'année de naissance → l'âge peut être faux de ±1 an
        # selon si l'anniversaire est passé ou non ; on expose l'année brute.
        "naissance_annee":       naissance_annee,
    }


# ── Profil complet ────────────────────────────────────────────────────────────

def get_player_profile(player_id: str) -> dict | None:
    """
    Construit le profil complet d'un joueur :
      - Infos de base (+ champs enrichis pipeline JSON)
      - KPIs (nb tournois, points totaux, position moyenne)
      - Trophy shelf (top 5 par catégorie classique)
      - Top partenaires
      - Distribution des positions
      - Championnats (meilleurs résultats)
      - Pyramide, percentile, stats dérivées, parcours détaillé (fiche v2)
    """
    # ── Infos de base ─────────────────────────────────────────────────────
    base = fetchone(
        """
        SELECT id_fft, nom, prenom, classement, meilleur_classement,
               variation_classement, classement_date,
               club_nom, ville, sexe, naissance, scraped_at,
               points, ligue, comite, dept_num, age, nationalite,
               actif, categorie_age, niveau_galaxie
        FROM joueurs WHERE id_fft = ?
        """,
        (player_id,),
    )
    if not base:
        return None

    joueur = _fmt_joueur(base)
    # scraped_at NULL = joueur connu via CSV uniquement, sans historique de participations
    joueur["scraped_at"]   = base.get("scraped_at")
    joueur["csv_only"]     = base.get("scraped_at") is None
    # Champs enrichis (pipeline JSON) — utilisés par la fiche joueur
    joueur["points_officiels"] = base.get("points")
    joueur["ligue"]            = base.get("ligue") or ""
    joueur["comite"]           = base.get("comite") or ""
    joueur["dept_num"]         = base.get("dept_num") or ""
    joueur["age"]              = base.get("age")
    joueur["nationalite"]      = base.get("nationalite") or ""
    joueur["actif"]            = bool(base.get("actif")) if base.get("actif") is not None else None
    joueur["categorie_age"]    = base.get("categorie_age")
    joueur["niveau_galaxie"]   = base.get("niveau_galaxie") or ""

    # ── Toutes ses participations ─────────────────────────────────────────
    # Tri chronologique : DD/MM/YYYY → YYYYMMDD via SUBSTR, safe sur PG et SQLite.
    # TO_DATE() est évité car il crashe sur les dates vides/malformées en PG.
    _date_order = "SUBSTR(p.date_tournoi,7,4)||SUBSTR(p.date_tournoi,4,2)||SUBSTR(p.date_tournoi,1,2)"
    parts = fetchall(
        f"""
        SELECT p.position, p.points, p.partenaire_id, p.partenaire_nom,
               p.date_tournoi, p.type, t.categorie, t.nom as tournoi_nom, p.id_tournoi,
               p.pris_en_compte, p.points_num, p.position_num,
               ts.niveau_points, ts.classement_meilleur AS tableau_meilleur,
               ts.nb_joueurs AS tableau_nb_joueurs
        FROM participations p
        JOIN tournois t ON p.id_tournoi = t.id_tournoi
        LEFT JOIN tournois_stats ts ON ts.id_tournoi = p.id_tournoi
        WHERE p.id_joueur = ?
        ORDER BY {_date_order} DESC
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
    partners_raw = defaultdict(lambda: {"nb": 0, "victoires": 0, "positions": [], "points": 0.0})
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
        try:
            partners_raw[(pid, nom)]["points"] += float(p["points_num"] or 0)
        except (ValueError, TypeError):
            pass

    # Batch-fetch partner info in a single query instead of N+1 individual queries
    all_partner_ids = [pid for (pid, _nom) in partners_raw]
    partner_info_map: dict = {}
    if all_partner_ids:
        placeholders = ",".join(["?" for _ in all_partner_ids])
        partner_rows = fetchall(
            f"SELECT id_fft, classement, club_nom FROM joueurs WHERE id_fft IN ({placeholders})",
            tuple(all_partner_ids),
        )
        partner_info_map = {r["id_fft"]: r for r in partner_rows}

    top_partners = []
    for (pid, nom), data in sorted(partners_raw.items(), key=lambda x: -x[1]["points"]):
        pos_list = data["positions"]
        pos_moy = round(sum(pos_list) / len(pos_list), 1) if pos_list else None
        taux_vic = round(data["victoires"] / data["nb"] * 100) if data["nb"] > 0 else 0

        partner_info = partner_info_map.get(pid, {})

        top_partners.append({
            "id":           pid,
            "nom_complet":  nom,
            "nb_tournois":  data["nb"],
            "nb_victoires": data["victoires"],
            "taux_victoire": taux_vic,
            "pos_moyenne":  pos_moy,
            "points":       round(data["points"]),
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

    # ── Historique mensuel des classements ───────────────────────────────
    try:
        hist_rows = fetchall(
            """
            SELECT mois, classement, variation
            FROM classements_historique
            WHERE id_joueur = ?
            ORDER BY mois ASC
            """,
            (player_id,),
        )
    except Exception:
        hist_rows = []  # table absente ou schéma différent — non bloquant
    rang_historique = [
        {
            "mois":       r["mois"],
            "classement": r["classement"],
            "variation":  r["variation"],
            "meilleur":   None,
        }
        for r in hist_rows
        if r["classement"] is not None
    ]

    # ── Pyramide — rang par environnement (dernier mois dispo) ────────────
    pyramide = {}
    try:
        pyr_rows = fetchall(
            """
            SELECT environnement, rang, rang_bas
            FROM rangs_pyramide
            WHERE id_fft = ?
              AND mois = (SELECT MAX(mois) FROM rangs_pyramide WHERE id_fft = ?)
            """,
            (player_id, player_id),
        )
        for r in pyr_rows:
            pyramide[r["environnement"]] = {
                "rang":  r["rang"],
                "total": r["rang_bas"],  # nb de joueurs dans cet environnement
            }
    except Exception:
        pyramide = {}

    # ── Percentile national (dans le bon pool H/F — règle d'or) ───────────
    percentile = None
    total_classes = None
    try:
        if joueur["classement"] and joueur["sexe"] in ("H", "F"):
            total_classes = fetchval(
                "SELECT COUNT(*) FROM joueurs WHERE sexe = ? AND classement IS NOT NULL",
                (joueur["sexe"],),
            )
            if total_classes:
                percentile = round(100 * joueur["classement"] / total_classes, 2)
    except Exception:
        pass

    # ── Stats dérivées du parcours ────────────────────────────────────────
    pos_nums = [p["position_num"] for p in parts if p.get("position_num")]
    meilleure_place = min(pos_nums) if pos_nums else None
    nb_top8 = sum(1 for p in pos_nums if p <= 8)

    # Répartition par niveau de tournoi (P25..P2000)
    niveaux_dist = defaultdict(int)
    for p in parts:
        nv = p.get("niveau_points")
        if nv:
            niveaux_dist[int(nv)] += 1
    niveaux_dist = dict(sorted(niveaux_dist.items()))

    # Évolution sur 12 mois (delta de classement ; négatif = progression)
    evolution_12m = None
    if len(rang_historique) >= 2:
        dernier = rang_historique[-1]["classement"]
        cible = rang_historique[-13] if len(rang_historique) >= 13 else rang_historique[0]
        if dernier is not None and cible["classement"] is not None:
            evolution_12m = dernier - cible["classement"]

    # Parcours détaillé (récent → ancien) avec niveau P et flag "comptée"
    parcours_detail = [
        {
            "id_tournoi":     p["id_tournoi"],
            "tournoi_nom":    p["tournoi_nom"] or "",
            "categorie":      p["categorie"],
            "niveau_points":  p.get("niveau_points"),
            "date":           p["date_tournoi"] or "",
            "type":           p["type"] or "",
            "position":       p["position"],
            "position_num":   p.get("position_num"),
            "points":         p.get("points_num") or p["points"],
            "partenaire":     p["partenaire_nom"] or "",
            "partenaire_id":  p["partenaire_id"],
            "pris_en_compte": bool(p.get("pris_en_compte")),
            "tableau_nb_joueurs": p.get("tableau_nb_joueurs"),
            "tableau_meilleur":   p.get("tableau_meilleur"),
        }
        for p in parts
    ]
    nb_comptees = sum(1 for p in parcours_detail if p["pris_en_compte"])

    # Indice de difficulté par tournoi (table tournois_rating, si construite)
    try:
        tids = list({p["id_tournoi"] for p in parcours_detail if p["id_tournoi"]})
        rmap = {}
        if tids:
            ph = ",".join(["?"] * len(tids))
            rmap = {
                r["id_tournoi"]: r
                for r in fetchall(
                    f"SELECT id_tournoi, indice_niveau, surcote_niveau, indice_categorie, "
                    f"niveau_effectif, multi_board, equipes FROM tournois_rating WHERE id_tournoi IN ({ph})",
                    tuple(tids),
                )
            }
        for p in parcours_detail:
            rr = rmap.get(p["id_tournoi"])
            p["indice_niveau"]    = rr["indice_niveau"]    if rr else None
            p["surcote_niveau"]   = rr["surcote_niveau"]   if rr else None
            p["indice_categorie"] = rr["indice_categorie"] if rr else None
    except Exception:
        for p in parcours_detail:
            p.setdefault("indice_niveau", None)
            p.setdefault("surcote_niveau", None)
            p.setdefault("indice_categorie", None)

    # Club où le tournoi a été joué (organisateur déduit, table tournois_club)
    try:
        _ctids = list({p["id_tournoi"] for p in parcours_detail if p["id_tournoi"]})
        cmap = {}
        if _ctids:
            ph_c = ",".join(["?"] * len(_ctids))
            cmap = {r["id_tournoi"]: r["club_nom"] for r in fetchall(
                f"SELECT id_tournoi, club_nom FROM tournois_club WHERE id_tournoi IN ({ph_c})", tuple(_ctids))}
        for p in parcours_detail:
            p["club"] = cmap.get(p["id_tournoi"])
    except Exception:
        for p in parcours_detail:
            p.setdefault("club", None)

    # Perf pondérée par la difficulté du plateau (relevé pour sa catégorie)
    for p in parcours_detail:
        ic = p.get("indice_categorie")
        pts = float(p.get("points") or 0)
        w = (0.5 + ic / 100.0) if ic is not None else 1.0
        p["perf_ponderee"] = round(pts * w)
    base_counted = sum(float(p.get("points") or 0) for p in parcours_detail if p["pris_en_compte"])
    points_ponderes = sum(p["perf_ponderee"] for p in parcours_detail if p["pris_en_compte"])
    perf_ratio = round(points_ponderes / base_counted, 3) if base_counted else None

    # Top performances (comptées) triées par points — "trophy shelf" v2
    meilleures_perfs = sorted(
        [p for p in parcours_detail if p["pris_en_compte"] and p["points"]],
        key=lambda p: -float(p["points"]),
    )[:3]

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
        "rang_historique":   rang_historique,
        # ── Champs fiche v2 ──
        "pyramide":          pyramide,
        "percentile":        percentile,
        "total_classes":     total_classes,
        "meilleure_place":   meilleure_place,
        "nb_top8":           nb_top8,
        "nb_comptees":       nb_comptees,
        "niveaux_dist":      niveaux_dist,
        "evolution_12m":     evolution_12m,
        "parcours_detail":   parcours_detail,
        "meilleures_perfs":  meilleures_perfs,
        "points_ponderes":   points_ponderes,
        "perf_ratio":        perf_ratio,
    }
