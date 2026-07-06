"""diag.py — pourquoi les bilans échouent. Lance : python diag.py"""
import sqlite3
c = sqlite3.connect('tenup.db')

print("=== Répartition des statuts dans scrape_queue ===")
for st, n in c.execute("SELECT statut, COUNT(*) FROM scrape_queue GROUP BY statut ORDER BY COUNT(*) DESC"):
    print(f"  {st:12} {n:,}")

print("\n=== Top messages d'erreur ===")
rows = c.execute("""SELECT error, COUNT(*) n FROM scrape_queue
                    WHERE statut='error' GROUP BY error ORDER BY n DESC LIMIT 10""").fetchall()
for err, n in rows:
    print(f"  {n:>6}  ->  {repr(err)}")

print("\n=== Exemple d'id_fft en erreur (pour test manuel) ===")
ex = c.execute("SELECT id_fft FROM scrape_queue WHERE statut='error' LIMIT 5").fetchall()
print("  ", [r[0] for r in ex])

print("\n=== Ces id_fft existent-ils dans joueurs (de la liste fraîche) ? ===")
for (idf,) in ex:
    r = c.execute("SELECT nom, prenom, classement, dernier_mois_vu FROM joueurs WHERE id_fft=?", (idf,)).fetchone()
    print(f"  {idf}: {r}")
