import sqlite3, time

DB = '/sessions/festive-exciting-faraday/mnt/backend/tenup.db'
c = sqlite3.connect(DB)

# checkpoint WAL d'abord
c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
print("WAL checkpointé")

sql = """UPDATE participations SET expiration_date = CASE expiration
  WHEN 'janvier 2026' THEN '2026-01' WHEN 'février 2026' THEN '2026-02'
  WHEN 'mars 2026' THEN '2026-03' WHEN 'avril 2026' THEN '2026-04'
  WHEN 'mai 2026' THEN '2026-05' WHEN 'juin 2026' THEN '2026-06'
  WHEN 'juillet 2026' THEN '2026-07' WHEN 'août 2026' THEN '2026-08'
  WHEN 'septembre 2026' THEN '2026-09' WHEN 'octobre 2026' THEN '2026-10'
  WHEN 'novembre 2026' THEN '2026-11' WHEN 'décembre 2026' THEN '2026-12'
  WHEN 'janvier 2027' THEN '2027-01' WHEN 'février 2027' THEN '2027-02'
  WHEN 'mars 2027' THEN '2027-03' WHEN 'avril 2027' THEN '2027-04'
  WHEN 'mai 2027' THEN '2027-05' WHEN 'juin 2027' THEN '2027-06'
  WHEN 'juillet 2027' THEN '2027-07' WHEN 'août 2027' THEN '2027-08'
  END WHERE expiration IS NOT NULL"""

t0 = time.time()
c.execute(sql)
n = c.execute('SELECT changes()').fetchone()[0]
c.commit()
print(f"Done: {n} rows updated en {time.time()-t0:.1f}s")

# Vérif
for r in c.execute("SELECT expiration_date, COUNT(*) FROM participations GROUP BY expiration_date LIMIT 5").fetchall():
    print(r)
c.close()
