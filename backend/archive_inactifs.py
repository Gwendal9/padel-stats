"""
Crée la table joueurs_inactifs et y déplace les entrées 'error' de scrape_queue
dont le profil est vide (compte FFT probablement supprimé).
"""
import sqlite3

DB = '/sessions/festive-exciting-faraday/mnt/backend/tenup.db'
c = sqlite3.connect(DB)

c.execute("PRAGMA wal_checkpoint(TRUNCATE)")

# Créer la table si elle n'existe pas
c.execute("""
    CREATE TABLE IF NOT EXISTS joueurs_inactifs (
        id_fft      TEXT PRIMARY KEY,
        error       TEXT,
        retries     INTEGER,
        added_at    TEXT,
        archived_at TEXT DEFAULT (datetime('now'))
    )
""")

# Insérer depuis scrape_queue (sans écraser si déjà présent)
c.execute("""
    INSERT OR IGNORE INTO joueurs_inactifs (id_fft, error, retries, added_at)
    SELECT id_fft, error, retries, added_at
    FROM scrape_queue
    WHERE statut = 'error'
""")
n_inserted = c.execute('SELECT changes()').fetchone()[0]
print(f"Archivés : {n_inserted} joueurs inactifs")

# Supprimer de scrape_queue
c.execute("DELETE FROM scrape_queue WHERE statut = 'error'")
n_deleted = c.execute('SELECT changes()').fetchone()[0]
print(f"Supprimés de scrape_queue : {n_deleted}")

c.commit()

# Stats finales
total = c.execute('SELECT COUNT(*) FROM joueurs_inactifs').fetchone()[0]
print(f"Total joueurs_inactifs : {total}")
sq = c.execute("SELECT statut, COUNT(*) FROM scrape_queue GROUP BY statut").fetchall()
print(f"scrape_queue après : {sq}")

c.close()
print("DONE")
