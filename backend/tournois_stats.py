"""
tournois_stats.py — Calcule le "poids" de chaque tournoi pour faire des stats.

Crée/remplit la table `tournois_stats` avec, par tournoi :
  - niveau_points    : le niveau P normalisé (25, 50, 100, 250, 500, 1000, 1500, 2000)
                       extrait de la catégorie/nom (= poids NOMINAL, points à gagner)
  - est_par_equipes  : 1 si épreuve par équipes / interclubs / championnat
  - nb_joueurs       : nb de participants connus (joueurs scrapés ayant ce tournoi au bilan)
  - classement_moyen / classement_meilleur : force RÉELLE du tableau
  - points_total / points_max : points distribués / meilleure perf

⚠️ nb_joueurs = participants présents dans la base (joueurs classés & scrapés). Très bonne
   couverture mais pas un recensement parfait du tableau complet.

Usage :
    python tournois_stats.py            # construit la table tournois_stats
    python tournois_stats.py --show     # + affiche un aperçu
"""
import sqlite3, sys, os, re

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tenup.db')
SHOW = '--show' in sys.argv


def niveau_points(categorie, nom):
    """Extrait le niveau P (25..2000) depuis 'P250', 'Championnat... (P500)', etc."""
    for src in (categorie or '', nom or ''):
        m = re.search(r'P\s?(\d{2,4})', src.upper())
        if m:
            v = int(m.group(1))
            if v in (25, 50, 100, 250, 500, 1000, 1500, 2000):
                return v
    return None


def est_par_equipes(categorie, nom):
    s = f"{categorie or ''} {nom or ''}".upper()
    return 1 if ('EQP' in s or 'EQUIPE' in s or 'ÉQUIPE' in s or 'INTERCLUB' in s
                 or 'CHAMPIONNAT' in s or s.strip() == 'PD') else 0


def main():
    c = sqlite3.connect(DB, isolation_level=None)
    c.execute('''CREATE TABLE IF NOT EXISTS tournois_stats (
        id_tournoi TEXT PRIMARY KEY, nom TEXT, categorie TEXT,
        niveau_points INTEGER, est_par_equipes INTEGER, nb_joueurs INTEGER,
        classement_moyen INTEGER, classement_meilleur INTEGER,
        points_total REAL, points_max REAL )''')
    c.execute("CREATE INDEX IF NOT EXISTS idx_tstats_niveau ON tournois_stats(niveau_points)")

    print("⏳ Agrégation par tournoi…")
    rows = c.execute('''
        SELECT p.id_tournoi, t.nom, t.categorie,
               COUNT(DISTINCT p.id_joueur) nb,
               AVG(j.classement) cl_moy, MIN(j.classement) cl_best,
               SUM(p.points_num) pts_tot, MAX(p.points_num) pts_max
        FROM participations p
        JOIN tournois t ON t.id_tournoi = p.id_tournoi
        JOIN joueurs  j ON j.id_fft     = p.id_joueur
        GROUP BY p.id_tournoi''').fetchall()

    data = []
    for idt, nom, cat, nb, clmoy, clbest, ptot, pmax in rows:
        data.append((idt, nom, cat, niveau_points(cat, nom), est_par_equipes(cat, nom),
                     nb, int(clmoy) if clmoy is not None else None, clbest, ptot, pmax))

    c.execute("BEGIN")
    c.execute("DELETE FROM tournois_stats")
    c.executemany('''INSERT INTO tournois_stats
        (id_tournoi, nom, categorie, niveau_points, est_par_equipes, nb_joueurs,
         classement_moyen, classement_meilleur, points_total, points_max)
        VALUES (?,?,?,?,?,?,?,?,?,?)''', data)
    c.execute("COMMIT")
    print(f"✓ tournois_stats rempli : {len(data):,} tournois")

    sansniv = sum(1 for d in data if d[3] is None)
    print(f"  sans niveau P identifié : {sansniv:,} ({100*sansniv/max(len(data),1):.1f}%)")

    if SHOW:
        print("\n— Nb de tournois par niveau —")
        for niv, nb in c.execute("SELECT niveau_points, COUNT(*) FROM tournois_stats GROUP BY niveau_points ORDER BY niveau_points"):
            print(f"   P{niv}: {nb:,}" if niv else f"   (non identifié): {nb:,}")
        print("\n— Force moyenne du tableau par niveau (classement moyen des participants) —")
        for niv, clm, n in c.execute('''SELECT niveau_points, CAST(AVG(classement_moyen) AS INT), COUNT(*)
                                         FROM tournois_stats WHERE niveau_points IS NOT NULL AND nb_joueurs>=4
                                         GROUP BY niveau_points ORDER BY niveau_points'''):
            print(f"   P{niv}: classement moyen ~{clm}  ({n:,} tournois)")


if __name__ == '__main__':
    main()
