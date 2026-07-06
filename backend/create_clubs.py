"""
Crée la table clubs à partir des club_nom distincts dans joueurs,
puis ajoute une FK club_id dans joueurs.
"""
import sqlite3, re

DB = '/sessions/festive-exciting-faraday/mnt/backend/tenup.db'
c = sqlite3.connect(DB)

c.execute("PRAGMA wal_checkpoint(TRUNCATE)")

# 1. Créer la table clubs
c.execute("""
    CREATE TABLE IF NOT EXISTS clubs (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        nom         TEXT NOT NULL UNIQUE,
        ville       TEXT,
        created_at  TEXT DEFAULT (datetime('now'))
    )
""")
print("Table clubs créée")

# 2. Remplir avec les clubs distincts (non nuls) de joueurs
c.execute("""
    INSERT OR IGNORE INTO clubs (nom)
    SELECT DISTINCT club_nom
    FROM joueurs
    WHERE club_nom IS NOT NULL AND club_nom != ''
""")
n = c.execute('SELECT changes()').fetchone()[0]
c.commit()
print(f"Clubs insérés : {n}")

# 3. Ajouter colonne club_id dans joueurs si elle n'existe pas
cols = [r[1] for r in c.execute('PRAGMA table_info(joueurs)').fetchall()]
if 'club_id' not in cols:
    c.execute('ALTER TABLE joueurs ADD COLUMN club_id INTEGER REFERENCES clubs(id)')
    print("Colonne club_id ajoutée à joueurs")

c.commit()

# Stats
total_clubs = c.execute('SELECT COUNT(*) FROM clubs').fetchone()[0]
print(f"Total clubs : {total_clubs}")

# Top 10
print("\nTop 10 clubs par nb de joueurs :")
for r in c.execute("""
    SELECT cl.nom, COUNT(j.id_fft) as n
    FROM clubs cl
    JOIN joueurs j ON j.club_nom = cl.nom
    GROUP BY cl.id ORDER BY n DESC LIMIT 10
""").fetchall():
    print(f"  {r[1]:>5}  {r[0]}")

c.close()
print("DONE")
