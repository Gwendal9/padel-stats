"""
tournois_rating.py — Indice de difficulté / niveau réel d'un tournoi.

Va plus loin que `tournois_stats.classement_moyen` : la moyenne brute des rangs est trompeuse
(le rang n'est pas linéaire, et une moyenne masque la distribution — une paire 2000+40000 a la
même moyenne qu'une paire 22000+22000 sans avoir le même niveau).

Méthode (voir DOC_TOURNOIS_RATING.md) :
  1. rang -> score de niveau   s = ln(N / rang)   (N = taille du pool H ou F ; règle H/F séparés)
  2. niveau d'une paire        paire = W*s_max + (1-W)*s_min   (W>=0.5 : le fort tire la paire)
  3. force du plateau          F = BETA*plafond + (1-BETA)*profondeur
                               plafond  = moyenne des meilleures paires (top fraction)
                               profondeur = médiane des paires
  4. indice_niveau (0-100)     F normalisé par ln(N) du pool  (ABSOLU, robuste à l'étiquetage)
  5. surcote_niveau            z-score de F parmi les tournois du même niveau EFFECTIF

⚠️ Étiquetage : un même id_tournoi peut héberger plusieurs tableaux (ex. "P500H P100D"). Le
niveau nominal `niveau_points` (tournois_stats) ne reflète alors pas le plateau réel. On déduit
donc un `niveau_effectif` = P le plus haut trouvé dans le nom, et un flag `multi_board`. La
surcote est calculée par niveau EFFECTIF (sinon des P500 mal étiquetés polluent les P100).

Écrit la table `tournois_rating`. À lancer sous Windows (écrit la base volumineuse).

Usage :
    python tournois_rating.py            # construit la table
    python tournois_rating.py --show     # + aperçu
"""
import sqlite3, os, sys, math, statistics, re
from collections import defaultdict

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tenup.db')
SHOW = '--show' in sys.argv

# ── Paramètres calibrables ──────────────────────────────────────────────────
W         = 0.60   # pondération vers le joueur le plus fort dans une paire (0.5 = moyenne géo)
BETA      = 0.60   # plafond vs profondeur dans la force du plateau
TOPK_FRAC = 0.25   # fraction de "meilleures paires" pour le plafond
TOPK_MIN  = 4      # au moins 4 paires pour le plafond

VALID_P = {25, 50, 100, 250, 500, 1000, 1500, 2000}
_P_RE = re.compile(r'P\s?(\d{2,4})')


def levels_in_name(nom):
    """Tous les niveaux P (valides) présents dans un nom de tournoi, dédupliqués."""
    out = set()
    for m in _P_RE.finditer((nom or '').upper()):
        v = int(m.group(1))
        if v in VALID_P:
            out.add(v)
    return out


def main():
    c = sqlite3.connect(DB, isolation_level=None)

    # Taille des pools H / F (joueurs classés) -> N pour s = ln(N/rang)
    N = {}
    for sexe, n in c.execute(
        "SELECT sexe, COUNT(*) FROM joueurs WHERE classement IS NOT NULL "
        "AND sexe IN ('H','F') GROUP BY sexe"
    ):
        N[sexe] = n
    if not N:
        print("❌ Aucun joueur classé trouvé."); return
    lnN = {s: math.log(n) for s, n in N.items() if n > 0}
    print(f"Pools : {N}")

    def skill(rank, sexe):
        if not rank or rank < 1 or sexe not in N:
            return None
        return math.log(N[sexe] / rank)   # 0 (dernier) .. ln(N) (rang 1)

    # niveau_points nominal (tournois_stats) + nom (tournois) -> niveau effectif
    niveau_nom = {}    # id -> niveau nominal
    try:
        for tid, nv in c.execute("SELECT id_tournoi, niveau_points FROM tournois_stats"):
            niveau_nom[tid] = nv
    except sqlite3.OperationalError:
        pass
    noms = {}
    for tid, nom in c.execute("SELECT id_tournoi, nom FROM tournois"):
        noms[tid] = nom
    equipes_map = {}
    try:
        for tid, eq in c.execute("SELECT id_tournoi, est_par_equipes FROM tournois_stats"):
            equipes_map[tid] = eq or 0
    except sqlite3.OperationalError:
        pass

    def niveau_eff_and_flag(tid):
        nominal = niveau_nom.get(tid)
        lv = levels_in_name(noms.get(tid))
        nom_max = max(lv) if lv else None
        eff = max([x for x in (nominal, nom_max) if x], default=None)
        multi = 1 if (len(lv) > 1 or (nominal and nom_max and nom_max != nominal)) else 0
        return eff, multi

    # Table de sortie (recalculée intégralement à chaque passage)
    c.execute("DROP TABLE IF EXISTS tournois_rating")
    c.execute('''CREATE TABLE IF NOT EXISTS tournois_rating (
        id_tournoi      TEXT PRIMARY KEY,
        niveau_points   INTEGER,   -- nominal (tournois_stats)
        niveau_effectif INTEGER,   -- P le plus haut détecté (nom + nominal)
        multi_board     INTEGER,   -- 1 si plusieurs tableaux/niveaux dans le nom
        sexe            TEXT,
        nb_paires       INTEGER,
        skill_plafond   REAL,
        skill_profondeur REAL,
        force_plateau   REAL,
        indice_niveau   REAL,      -- 0-100, absolu (dans le pool)
        surcote_niveau  REAL,      -- z-score vs tournois de même niveau effectif
        indice_categorie REAL,     -- 0-100 : rang PARMI les tournois de son niveau P + sexe
        equipes         INTEGER    -- 1 si epreuve par equipes / championnat
    )''')
    c.execute("CREATE INDEX IF NOT EXISTS idx_trating_niveau ON tournois_rating(niveau_effectif)")

    print("⏳ Lecture des participations (joueur + partenaire)…")
    cur = c.execute('''
        SELECT p.id_tournoi, p.id_joueur, j.classement, j.sexe,
               p.partenaire_id, jp.classement AS prank, jp.sexe AS psexe, p.type
        FROM participations p
        JOIN joueurs  j  ON j.id_fft  = p.id_joueur
        LEFT JOIN joueurs jp ON jp.id_fft = p.partenaire_id
        ORDER BY p.id_tournoi
    ''')

    results = []   # (tid, niv_nom, niv_eff, multi, sexe_dom, nb_paires, plafond, profondeur, force, equipes)
    mixte_count = [0]

    def flush(tid, rows):
        # Construit les paires ET compte les paires MIXTES (1 H + 1 F) pour détecter
        # un tournoi mixte de façon robuste (le type DX et le mot "MIXTE" manquent souvent).
        type_count = defaultdict(int)
        for r in rows:
            type_count[(r[6] or '').upper()] += 1
        pairs = {}
        sexe_count = defaultdict(int)
        pair_total = 0
        pair_mixed = 0
        for (idj, rank, sexe, pid, prank, psexe, typ) in rows:
            s = skill(rank, sexe)
            if s is None:
                continue
            sexe_count[sexe] += 1
            if pid and prank:
                key = tuple(sorted((idj, pid)))
                if key not in pairs:
                    pair_total += 1
                    if sexe and psexe and sexe != psexe:
                        pair_mixed += 1
                sp = skill(prank, psexe or sexe)
                if sp is None:
                    sp = s
                hi, lo = (s, sp) if s >= sp else (sp, s)
                pairs[key] = W * hi + (1 - W) * lo
            else:
                pairs[('solo', idj)] = s

        # ── Détection MIXTE (exclusion) ──────────────────────────────────────
        nm = (noms.get(tid) or '').upper()
        dx = type_count.get('DX', 0); tot_t = sum(type_count.values())
        mixed_ratio = (pair_mixed / pair_total) if pair_total else 0.0
        # filet de sécurité : présence notable des deux sexes parmi les joueurs
        both = min(sexe_count.get('H', 0), sexe_count.get('F', 0))
        tot_s = sum(sexe_count.values())
        minor_ratio = (both / tot_s) if tot_s else 0.0
        if ('MIXTE' in nm or 'MIXED' in nm
                or (tot_t > 0 and dx > tot_t / 2)
                or mixed_ratio >= 0.4
                or minor_ratio >= 0.20):
            mixte_count[0] += 1
            return

        vals = sorted(pairs.values(), reverse=True)
        n = len(vals)
        if n == 0:
            return
        k = min(n, max(TOPK_MIN, math.ceil(TOPK_FRAC * n)))
        plafond = sum(vals[:k]) / k
        profondeur = statistics.median(vals)
        force = BETA * plafond + (1 - BETA) * profondeur
        sexe_dom = max(sexe_count, key=sexe_count.get) if sexe_count else None
        eff, multi = niveau_eff_and_flag(tid)
        results.append((tid, niveau_nom.get(tid), eff, multi, sexe_dom, n, plafond, profondeur, force, equipes_map.get(tid, 0)))

    cur_tid, buf = None, []
    for row in cur:
        tid = row[0]
        if tid != cur_tid:
            if cur_tid is not None:
                flush(cur_tid, buf)
            cur_tid, buf = tid, []
        buf.append(row[1:])
    if cur_tid is not None:
        flush(cur_tid, buf)

    print(f"✅ {len(results)} tournois notés ({mixte_count[0]} mixtes exclus).")

    # ── Surcote : z-score de la force par niveau EFFECTIF, baseline = mono-tableau ──
    by_niveau = defaultdict(list)
    for r in results:
        if not r[3] and not r[9]:          # ni multi_board ni equipes -> baseline propre
            by_niveau[(r[2], r[4])].append(r[8])   # cle = (niveau_effectif, sexe)  → regle H/F
    stats = {}
    for nv, forces in by_niveau.items():
        if len(forces) >= 2:
            stats[nv] = (statistics.mean(forces), statistics.pstdev(forces) or 1.0)

    import bisect
    cat_sorted = {k: sorted(v) for k, v in by_niveau.items()}   # cle = (niveau_eff, sexe)
    def _cat_pct(niv, sexe, force):
        arr = cat_sorted.get((niv, sexe))
        if not arr or len(arr) < 2:
            return None
        i = min(bisect.bisect_left(arr, force), len(arr) - 1)
        return round(100.0 * i / (len(arr) - 1), 1)

    out = []
    for (tid, niv_nom, niv_eff, multi, sexe_dom, n, plafond, profondeur, force, equipes) in results:
        ref_lnN = lnN.get(sexe_dom, max(lnN.values()))
        indice = max(0.0, min(100.0, 100.0 * force / ref_lnN))
        mu, sd = stats.get((niv_eff, sexe_dom), (force, 1.0))
        surcote = (force - mu) / sd
        cat = _cat_pct(niv_eff, sexe_dom, force)
        out.append((tid, niv_nom, niv_eff, multi, sexe_dom, n,
                    round(plafond, 4), round(profondeur, 4), round(force, 4),
                    round(indice, 2), round(surcote, 3), cat, equipes))

    c.execute("DELETE FROM tournois_rating")
    c.executemany(
        "INSERT INTO tournois_rating (id_tournoi, niveau_points, niveau_effectif, multi_board, "
        "sexe, nb_paires, skill_plafond, skill_profondeur, force_plateau, indice_niveau, "
        "surcote_niveau, indice_categorie, equipes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", out)
    print(f"\U0001f4be Table tournois_rating remplie ({len(out)} lignes).")

    if SHOW:
        _show(c)


def _show(c):
    print("\n— Aperçu —")
    print("Indice de niveau moyen par niveau effectif :")
    for nv, n, ind in c.execute(
        "SELECT niveau_effectif, COUNT(*), ROUND(AVG(indice_niveau),1) "
        "FROM tournois_rating WHERE niveau_effectif IS NOT NULL "
        "GROUP BY niveau_effectif ORDER BY niveau_effectif"):
        print(f"  P{nv:<5} {n:>6} tournois   indice moyen {ind}")

    nb_multi = c.execute("SELECT COUNT(*) FROM tournois_rating WHERE multi_board=1").fetchone()[0]
    print(f"\nTournois multi-tableaux détectés : {nb_multi}")

    print("\nVrais P100 les plus relevés (mono-tableau) :")
    for nom, ind, sur, npa in c.execute('''
        SELECT t.nom, r.indice_niveau, r.surcote_niveau, r.nb_paires
        FROM tournois_rating r JOIN tournois t ON t.id_tournoi=r.id_tournoi
        WHERE r.niveau_effectif=100 AND r.multi_board=0 AND r.equipes=0 AND r.nb_paires>=8
        ORDER BY r.surcote_niveau DESC LIMIT 8'''):
        print(f"  +{sur:>4.1f}σ  indice {ind:>5}  ({npa} paires)  {(nom or '')[:48]}")


if __name__ == '__main__':
    main()
