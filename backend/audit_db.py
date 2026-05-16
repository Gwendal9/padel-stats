"""
Audit de la base : volume, répartition H/F, anonymes, top classements.
"""
import sqlite3

conn = sqlite3.connect('tenup.db')
conn.execute('PRAGMA busy_timeout=5000')

print('━━━ Volume global ━━━')
total = conn.execute('SELECT COUNT(*) FROM joueurs').fetchone()[0]
parts = conn.execute('SELECT COUNT(*) FROM participations').fetchone()[0]
print(f'   joueurs       : {total:,}')
print(f'   participations: {parts:,}')

print('\n━━━ Répartition par sexe ━━━')
for row in conn.execute('SELECT sexe, COUNT(*) FROM joueurs GROUP BY sexe ORDER BY 2 DESC'):
    s = repr(row[0]) if row[0] else 'NULL'
    print(f'   sexe = {s:6s} : {row[1]:>8,}')

print('\n━━━ Anonymes ━━━')
n1 = conn.execute("SELECT COUNT(*) FROM joueurs WHERE nom='Anonyme'").fetchone()[0]
n2 = conn.execute("SELECT COUNT(*) FROM joueurs WHERE nom IS NULL OR nom=''").fetchone()[0]
print(f'   nom="Anonyme"  : {n1:,}')
print(f'   nom vide/null  : {n2:,}')

print('\n━━━ Anonymes par sexe ━━━')
for row in conn.execute("SELECT sexe, COUNT(*) FROM joueurs WHERE nom='Anonyme' GROUP BY sexe"):
    print(f'   sexe={row[0]!r}: {row[1]:,}')

print('\n━━━ Classement (avec/sans) ━━━')
n3 = conn.execute("SELECT COUNT(*) FROM joueurs WHERE classement IS NOT NULL").fetchone()[0]
n4 = conn.execute("SELECT COUNT(*) FROM joueurs WHERE classement IS NULL").fetchone()[0]
print(f'   avec classement: {n3:,}')
print(f'   sans classement: {n4:,}')

print('\n━━━ Distribution par sexe + classement ━━━')
for row in conn.execute('''
    SELECT sexe, COUNT(*) as n,
           MIN(classement), MAX(classement), AVG(classement)
    FROM joueurs WHERE classement IS NOT NULL
    GROUP BY sexe ORDER BY 2 DESC
'''):
    sx = row[0] if row[0] else 'NULL'
    print(f'   sexe={sx!r:8}: {row[1]:>7,} joueurs | min #{row[2]} max #{row[3]} moy #{row[4]:.0f}')

print('\n━━━ Top 10 hommes (par classement) ━━━')
for r in conn.execute("SELECT classement, prenom, nom, club_nom FROM joueurs WHERE sexe='H' AND classement IS NOT NULL AND nom!='Anonyme' ORDER BY classement LIMIT 10"):
    print(f'   #{r[0]:<5} {r[1]} {r[2]} ({r[3]})')

print('\n━━━ Top 10 femmes (par classement) ━━━')
for r in conn.execute("SELECT classement, prenom, nom, club_nom FROM joueurs WHERE sexe='F' AND classement IS NOT NULL AND nom!='Anonyme' ORDER BY classement LIMIT 10"):
    print(f'   #{r[0]:<5} {r[1]} {r[2]} ({r[3]})')

print('\n━━━ Sanity check : H et F au même rang ━━━')
print('   (vérifie si les classements sont vraiment séparés H/F)')
for r in conn.execute('''
    SELECT classement, COUNT(DISTINCT sexe) as n_sexes,
           SUM(CASE WHEN sexe='H' THEN 1 ELSE 0 END) as h,
           SUM(CASE WHEN sexe='F' THEN 1 ELSE 0 END) as f
    FROM joueurs WHERE classement IS NOT NULL AND classement <= 100
    GROUP BY classement HAVING n_sexes > 1 ORDER BY classement LIMIT 5
'''):
    print(f'   #{r[0]}: H={r[2]}, F={r[3]} → {r[1]} sexes au même rang')
