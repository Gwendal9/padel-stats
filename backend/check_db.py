import sqlite3

conn = sqlite3.connect('tenup.db', timeout=10)

print("=== STATS ===")
total      = conn.execute("SELECT COUNT(*) FROM scrape_queue").fetchone()[0]
done       = conn.execute("SELECT COUNT(*) FROM scrape_queue WHERE statut='done'").fetchone()[0]
pending    = conn.execute("SELECT COUNT(*) FROM scrape_queue WHERE statut='pending'").fetchone()[0]
processing = conn.execute("SELECT COUNT(*) FROM scrape_queue WHERE statut='processing'").fetchone()[0]
errors     = conn.execute("SELECT COUNT(*) FROM scrape_queue WHERE statut='error'").fetchone()[0]
joueurs    = conn.execute("SELECT COUNT(*) FROM joueurs").fetchone()[0]
parts      = conn.execute("SELECT COUNT(*) FROM participations").fetchone()[0]
print(f"Queue : {done} done | {pending} pending | {processing} processing | {errors} erreurs | {total} total")
print(f"Joueurs : {joueurs} | Participations : {parts}")

print("\n=== PROCESSING BLOQUÉS ===")
rows = conn.execute("SELECT id_fft, worker_id FROM scrape_queue WHERE statut='processing'").fetchall()
print(f"  {len(rows)} joueur(s) bloqués" if rows else "  Aucun ✅")
for r in rows:
    print(f"  {r[0]} | worker:{r[1]}")

print("\n=== DOUBLONS PARTICIPATIONS ===")
dups = conn.execute("""
    SELECT id_joueur, id_tournoi, COUNT(*)
    FROM participations
    GROUP BY id_joueur, id_tournoi
    HAVING COUNT(*) > 1
""").fetchall()
print(f"  {len(dups)} doublon(s)" if dups else "  Aucun doublon ✅")

print("\n=== DOUBLONS JOUEURS ===")
dups2 = conn.execute("""
    SELECT id_fft, COUNT(*)
    FROM joueurs
    GROUP BY id_fft
    HAVING COUNT(*) > 1
""").fetchall()
print(f"  {len(dups2)} doublon(s)" if dups2 else "  Aucun doublon ✅")

print("\n=== 10 DERNIERS SCRAPÉS ===")
for r in conn.execute("SELECT prenom, nom, scraped_at FROM joueurs ORDER BY scraped_at DESC LIMIT 10").fetchall():
    print(f"  {r[0]} {r[1]} — {r[2]}")

conn.close()
print("\nDone.")
