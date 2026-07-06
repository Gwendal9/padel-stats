"""diag2.py — où sont les 171k joueurs frais ? Lance : python diag2.py"""
import sqlite3
c = sqlite3.connect('tenup.db')

print("=== joueurs : valeurs de dernier_mois_vu ===")
for v, n in c.execute("SELECT dernier_mois_vu, COUNT(*) FROM joueurs GROUP BY dernier_mois_vu ORDER BY COUNT(*) DESC LIMIT 10"):
    print(f"  {repr(v):14} {n:,}")

print("\n=== scrape_queue : par statut ===")
for st, n in c.execute("SELECT statut, COUNT(*) FROM scrape_queue GROUP BY statut ORDER BY COUNT(*) DESC"):
    print(f"  {st:12} {n:,}")

print("\n=== croisement queue(pending) x joueurs.dernier_mois_vu ===")
rows = c.execute("""
    SELECT j.dernier_mois_vu, COUNT(*) FROM scrape_queue q
    JOIN joueurs j ON j.id_fft = q.id_fft
    WHERE q.statut='pending'
    GROUP BY j.dernier_mois_vu ORDER BY COUNT(*) DESC LIMIT 10""").fetchall()
for v, n in rows:
    print(f"  pending & dernier_mois_vu={repr(v):14} -> {n:,}")

print("\n=== joueurs vus en 2026-06 : quel statut dans la queue ? ===")
rows = c.execute("""
    SELECT COALESCE(q.statut,'(absent de la queue)'), COUNT(*) FROM joueurs j
    LEFT JOIN scrape_queue q ON q.id_fft = j.id_fft
    WHERE j.dernier_mois_vu='2026-06'
    GROUP BY q.statut ORDER BY COUNT(*) DESC""").fetchall()
for st, n in rows:
    print(f"  {st:28} -> {n:,}")

print("\n=== combien de joueurs 2026-06 ont déjà des points (bilan fait) ? ===")
print("  avec points:", c.execute("SELECT COUNT(*) FROM joueurs WHERE dernier_mois_vu='2026-06' AND points IS NOT NULL").fetchone()[0])
print("  sans points:", c.execute("SELECT COUNT(*) FROM joueurs WHERE dernier_mois_vu='2026-06' AND points IS NULL").fetchone()[0])
