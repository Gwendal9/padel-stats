import sqlite3
conn = sqlite3.connect('tenup.db')

print("=== STRUCTURE JOUEURS ===")
for r in conn.execute("PRAGMA table_info(joueurs)").fetchall():
    print(f"  {r[1]} ({r[2]})")

print("\n=== EXEMPLE JOUEURS ===")
for r in conn.execute("SELECT * FROM joueurs WHERE nom != '' LIMIT 3").fetchall():
    print(f"  {r}")

print("\n=== STRUCTURE PARTICIPATIONS ===")
for r in conn.execute("PRAGMA table_info(participations)").fetchall():
    print(f"  {r[1]} ({r[2]})")

print("\n=== EXEMPLE PARTICIPATIONS (joueur avec le plus de tournois) ===")
top = conn.execute("SELECT id_joueur, COUNT(*) as c FROM participations GROUP BY id_joueur ORDER BY c DESC LIMIT 1").fetchone()
print(f"  Joueur {top[0]} — {top[1]} participations")
for r in conn.execute("SELECT * FROM participations WHERE id_joueur=? LIMIT 5", (top[0],)).fetchall():
    print(f"  {r}")

print("\n=== STRUCTURE TOURNOIS ===")
for r in conn.execute("PRAGMA table_info(tournois)").fetchall():
    print(f"  {r[1]} ({r[2]})")

print("\n=== EXEMPLE TOURNOIS ===")
for r in conn.execute("SELECT * FROM tournois LIMIT 5").fetchall():
    print(f"  {r}")

print("\n=== RICHESSE DES DONNÉES ===")
empty = "''"
print(f"  Joueurs avec ville       : {conn.execute('SELECT COUNT(*) FROM joueurs WHERE ville IS NOT NULL AND ville != ' + empty).fetchone()[0]}")
print(f"  Joueurs avec classement  : {conn.execute('SELECT COUNT(*) FROM joueurs WHERE classement IS NOT NULL').fetchone()[0]}")
print(f"  Joueurs avec sexe        : {conn.execute('SELECT COUNT(*) FROM joueurs WHERE sexe IS NOT NULL AND sexe != ' + empty).fetchone()[0]}")
print(f"  Joueurs avec naissance   : {conn.execute('SELECT COUNT(*) FROM joueurs WHERE naissance IS NOT NULL AND naissance != ' + empty).fetchone()[0]}")
print(f"  Parts avec partenaire_id : {conn.execute('SELECT COUNT(*) FROM participations WHERE partenaire_id IS NOT NULL AND partenaire_id != ' + empty).fetchone()[0]}")
print(f"  Parts avec position      : {conn.execute('SELECT COUNT(*) FROM participations WHERE position IS NOT NULL AND position != ' + empty).fetchone()[0]}")
print(f"  Parts avec points        : {conn.execute('SELECT COUNT(*) FROM participations WHERE points IS NOT NULL AND points != ' + empty).fetchone()[0]}")
print(f"  Tournois uniques         : {conn.execute('SELECT COUNT(*) FROM tournois').fetchone()[0]}")

print("\n=== CATÉGORIES DE TOURNOIS ===")
for r in conn.execute("SELECT DISTINCT categorie, COUNT(*) as c FROM tournois GROUP BY categorie ORDER BY c DESC LIMIT 10").fetchall():
    print(f"  {r[0]} — {r[1]} tournois")

print("\n=== POSITIONS (résultats) ===")
for r in conn.execute("SELECT position, COUNT(*) as c FROM participations WHERE position != '' GROUP BY position ORDER BY c DESC LIMIT 10").fetchall():
    print(f"  '{r[0]}' — {r[1]} fois")

conn.close()
