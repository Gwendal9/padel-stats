"""
suggester.py — Suggesteur de partenaires.

Critères de scoring (sur 100) :
  Priorité 1 : Même ville (+40) / même département (+25) / même région (+10)
  Priorité 1 : Niveau proche — classement ±20% (+30 → 0 décroissant)
  Priorité 2 : Jamais joué ensemble (requis — on exclut si déjà partenaires)
  Priorité 2 : Joueur actif (nb_tournois ≥ 3 requis)
  Bonus      : Ami d'ami réseau (+15)
  Bonus      : Âge proche ±5 ans (+5)
"""
import datetime
from collections import defaultdict

from db import fetchall, fetchone
from graph_engine import engine


def suggest_partners(player_id: str, n: int = 10) -> list[dict]:
    """
    Retourne les `n` meilleurs partenaires suggérés pour un joueur.
    Le graphe doit être chargé avant l'appel (engine.load()).
    """
    # ── Infos du joueur de référence ──────────────────────────────────────
    ref = fetchone(
        """
        SELECT id_fft, nom, prenom, classement, club_nom, ville, sexe, naissance
        FROM joueurs WHERE id_fft = ?
        """,
        (player_id,),
    )
    if not ref:
        return []

    ref_cl    = ref["classement"] or 0
    ref_ville = (ref["ville"] or "").upper().strip()
    ref_sexe  = ref["sexe"] or "H"
    _year     = datetime.date.today().year
    ref_age   = _year - int(ref["naissance"]) if ref["naissance"] and str(ref["naissance"]).isdigit() else None

    # ── Partenaires déjà joués (à exclure) ───────────────────────────────
    deja_joue = set()
    rows_dj = fetchall(
        "SELECT DISTINCT partenaire_id FROM participations WHERE id_joueur = ? AND partenaire_id != ''",
        (player_id,),
    )
    for r in rows_dj:
        deja_joue.add(r["partenaire_id"])

    # ── Amis d'amis (1 degré de séparation dans le graphe) ───────────────
    amis = set(engine.graph.get(player_id, {}).keys())          # partenaires directs
    amis_d_amis = set()
    for ami in amis:
        for voisin in engine.graph.get(ami, {}):
            if voisin != player_id:
                amis_d_amis.add(voisin)

    # Noms des amis directs (pour afficher "ami de X")
    ami_noms = {}
    for ami in amis:
        info = engine.player_info.get(ami, {})
        ami_noms[ami] = f"{info.get('prenom','')} {info.get('nom','')}".strip()

    # ── Candidats : joueurs du même sexe, actifs, classement plausible ───
    marge = max(ref_cl * 0.4, 5000)    # ±40% ou au moins ±5 000 places
    cl_min = max(1, ref_cl - marge)
    cl_max = ref_cl + marge

    candidats = fetchall(
        """
        SELECT j.id_fft, j.nom, j.prenom, j.classement, j.club_nom, j.ville,
               j.naissance, COUNT(DISTINCT p.id_tournoi) as nb_tournois
        FROM joueurs j
        LEFT JOIN participations p ON p.id_joueur = j.id_fft
        WHERE j.id_fft != ?
          AND j.sexe   = ?
          AND j.classement BETWEEN ? AND ?
          AND j.classement IS NOT NULL
        GROUP BY j.id_fft, j.nom, j.prenom, j.classement, j.club_nom, j.ville, j.naissance
        HAVING COUNT(DISTINCT p.id_tournoi) >= 3
        """,
        (player_id, ref_sexe, cl_min, cl_max),
    )

    # ── Scoring ───────────────────────────────────────────────────────────
    scored = []
    for c in candidats:
        cid = c["id_fft"]

        # Exclure les déjà joués
        if cid in deja_joue:
            continue

        score = 0
        tags  = []
        connexion = None

        # — Niveau proche (0 → 30 pts) —
        if ref_cl > 0:
            diff_pct = abs((c["classement"] or 0) - ref_cl) / ref_cl
            pts_niveau = max(0, int(30 * (1 - diff_pct / 0.4)))
            score += pts_niveau
            if diff_pct < 0.1:
                tags.append("niveau_très_proche")
            elif diff_pct < 0.2:
                tags.append("niveau_proche")

        # — Proximité géographique (0 → 40 pts) —
        c_ville = (c["ville"] or "").upper().strip()
        if ref_ville and c_ville:
            if c_ville == ref_ville:
                score += 40
                tags.append("même_ville")
            elif c.get("club_nom") == ref.get("club_nom") and ref.get("club_nom"):
                score += 35
                tags.append("même_club")

        # — Ami d'ami (+15 pts) —
        if cid in amis_d_amis:
            score += 15
            # Trouver le lien commun
            for ami in amis:
                if cid in engine.graph.get(ami, {}):
                    connexion = f"ami de {ami_noms.get(ami, ami)}"
                    break
            tags.append("ami_d_ami")

        # — Âge proche (+5 pts) —
        if ref_age and c.get("naissance") and str(c["naissance"]).isdigit():
            c_age = _year - int(c["naissance"])
            if abs(c_age - ref_age) <= 5:
                score += 5
                tags.append("âge_proche")

        scored.append({
            "id":          cid,
            "nom":         c["nom"] or "",
            "prenom":      c["prenom"] or "",
            "nom_complet": f"{c.get('prenom','')} {c.get('nom','')}".strip(),
            "classement":  c["classement"],
            "club":        c["club_nom"] or "",
            "ville":       c["ville"] or "",
            "nb_tournois": c["nb_tournois"],
            "score":       score,
            "tags":        tags,
            "connexion":   connexion,
            "deja_joue":   False,
        })

    # Trier par score décroissant, couper à n
    scored.sort(key=lambda x: -x["score"])
    return scored[:n]
