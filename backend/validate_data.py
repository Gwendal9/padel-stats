"""
validate_data.py — Contrôle qualité des données scrapées (API JSON).
Lance : python validate_data.py
"""
import sqlite3
from datetime import datetime
c = sqlite3.connect('tenup.db')
MOIS = datetime.now().strftime('%Y-%m')

def q1(sql, *a): return c.execute(sql, a).fetchone()[0]

print("="*64)
print("1) COUVERTURE")
print("="*64)
tot = q1("SELECT COUNT(*) FROM joueurs")
vus = q1("SELECT COUNT(*) FROM joueurs WHERE dernier_mois_vu=?", MOIS)
bil = q1("SELECT COUNT(*) FROM joueurs WHERE dernier_mois_vu=? AND points IS NOT NULL", MOIS)
print(f"  joueurs total            : {tot:,}")
print(f"  vus dans la liste {MOIS}  : {vus:,}")
print(f"  avec bilan (points) faits: {bil:,}  ({100*bil/max(vus,1):.1f}% des vus)")
for sx in ('H', 'F'):
    n = q1("SELECT COUNT(*) FROM joueurs WHERE dernier_mois_vu=? AND sexe=?", MOIS, sx)
    nb = q1("SELECT COUNT(*) FROM joueurs WHERE dernier_mois_vu=? AND sexe=? AND points IS NOT NULL", MOIS, sx)
    print(f"  sexe {sx}: {n:,} vus, {nb:,} bilans faits")

print("\n" + "="*64)
print("2) COHÉRENCE DES VALEURS (joueurs avec bilan)")
print("="*64)
r = c.execute("SELECT MIN(classement),MAX(classement),MIN(points),MAX(points),AVG(points) "
              "FROM joueurs WHERE dernier_mois_vu=? AND points IS NOT NULL", (MOIS,)).fetchone()
print(f"  classement: min={r[0]} max={r[1]}   (attendu ~1..150000)")
print(f"  points    : min={r[2]} max={r[3]} moy={r[4]:.0f}")
print(f"  points négatifs (anomalie): {q1('SELECT COUNT(*) FROM joueurs WHERE points<0')}")
print(f"  sexe NULL parmi les vus   : {q1('SELECT COUNT(*) FROM joueurs WHERE dernier_mois_vu=? AND sexe IS NULL', MOIS)}")
print(f"  classement NULL parmi vus : {q1('SELECT COUNT(*) FROM joueurs WHERE dernier_mois_vu=? AND classement IS NULL', MOIS)}")

print("\n" + "="*64)
print("3) PARTICIPATIONS & PARTENAIRES")
print("="*64)
np = q1("SELECT COUNT(*) FROM participations")
npart = q1("SELECT COUNT(*) FROM participations WHERE partenaire_nom IS NOT NULL AND partenaire_nom!=''")
n_pec = q1("SELECT COUNT(*) FROM participations WHERE pris_en_compte=1")
n_ptsnum = q1("SELECT COUNT(*) FROM participations WHERE points_num IS NOT NULL")
n_tour = q1("SELECT COUNT(*) FROM tournois")
n_pyr = q1("SELECT COUNT(*) FROM rangs_pyramide")
dup = q1("SELECT COUNT(*) FROM (SELECT id_joueur,id_tournoi,COUNT(*) n FROM participations GROUP BY id_joueur,id_tournoi HAVING n>1)")
print(f"  participations total          : {np:,}")
print(f"  avec partenaire renseigné     : {npart:,}  ({100*npart/max(np,1):.1f}%)")
print(f"  pris_en_compte=1              : {n_pec:,}")
print(f"  points_num renseigné          : {n_ptsnum:,}")
print(f"  doublons (joueur,tournoi)     : {dup}   (doit être 0)")
print(f"  tournois distincts            : {n_tour:,}")
print(f"  rangs_pyramide (lignes)       : {n_pyr:,}")
envs = c.execute("SELECT environnement,COUNT(*) FROM rangs_pyramide GROUP BY environnement").fetchall()
print(f"  environnements pyramide       : {dict(envs)}")

print("\n" + "="*64)
print("4) HISTORIQUE DE CLASSEMENT (backfill calculs)")
print("="*64)
for mois, n in c.execute("SELECT mois,COUNT(*) FROM classements_historique GROUP BY mois ORDER BY mois DESC LIMIT 8"):
    print(f"  {mois} : {n:,} lignes")

print("\n" + "="*64)
print("5) RECOUPEMENT PROFILS CONNUS")
print("="*64)
for idf, attendu in [('7633273415', 'Gwendal ROLLAND ~#16652, 410 pts, ~10 tournois'),
                     ('1953828852', 'Mathis TAMISIER ~#7501'),
                     ('1329333964', 'Thomas LEYGUE #1')]:
    j = c.execute("SELECT nom,prenom,classement,points,sexe FROM joueurs WHERE id_fft=?", (idf,)).fetchone()
    nt = q1("SELECT COUNT(*) FROM participations WHERE id_joueur=?", idf)
    part = c.execute("SELECT partenaire_nom FROM participations WHERE id_joueur=? AND partenaire_nom!='' LIMIT 1", (idf,)).fetchone()
    print(f"  {idf}: {j}  | {nt} tournois | ex partenaire: {part[0] if part else '—'}")
    print(f"     (attendu: {attendu})")

print("\n" + "="*64)
print("6) ANOMALIES")
print("="*64)
print(f"  joueurs avec points mais 0 participation : "
      f"{q1('SELECT COUNT(*) FROM joueurs j WHERE j.points IS NOT NULL AND NOT EXISTS (SELECT 1 FROM participations p WHERE p.id_joueur=j.id_fft)'):,}")
print(f"  participations sans joueur (orphelines)  : "
      f"{q1('SELECT COUNT(*) FROM participations p WHERE NOT EXISTS (SELECT 1 FROM joueurs j WHERE j.id_fft=p.id_joueur)'):,}")
print("\nFini.")
