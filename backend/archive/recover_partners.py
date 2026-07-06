import sqlite3

conn = sqlite3.connect('tenup.db')
conn.execute("PRAGMA journal_mode=WAL")
cursor = conn.execute("""
    UPDATE scrape_queue
    SET statut = 'pending', error = NULL, worker_id = NULL, retries = 0
    WHERE statut = 'done'
      AND scraped_at IS NULL
      AND id_fft NOT IN (SELECT id_fft FROM joueurs)
""")
conn.commit()
print(f'Remis en pending : {cursor.rowcount:,} joueurs')
conn.close()
