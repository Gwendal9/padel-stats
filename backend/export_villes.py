"""
export_villes.py
────────────────
Exporte les villes non encore mappées dans clubs.json vers villes_manquantes.txt
Lance : python export_villes.py
"""
import sqlite3, json, os

DB_FILE    = os.path.join(os.path.dirname(__file__), 'tenup.db')
CLUBS_FILE = os.path.join(os.path.dirname(__file__), 'clubs.json')
OUT_FILE   = os.path.join(os.path.dirname(__file__), 'villes_manquantes.txt')

with open(CLUBS_FILE, encoding='utf-8') as f:
    clubs = json.load(f)
clubs.pop('_note', None)

conn = sqlite3.connect(DB_FILE)
conn.execute("PRAGMA journal_mode=WAL")
rows = conn.execute("""
    SELECT ville, COUNT(*) as c
    FROM joueurs
    WHERE ville IS NOT NULL AND ville != ''
    GROUP BY ville
    ORDER BY c DESC
""").fetchall()
conn.close()

manquantes = [(v, c) for v, c in rows if v not in clubs]

with open(OUT_FILE, 'w', encoding='utf-8') as f:
    f.write(f"{len(rows)} villes en base, {len(manquantes)} manquantes dans clubs.json\n\n")
    for v, c in manquantes:
        f.write(f"{c}\t{v}\n")

print(f"✅ {len(manquantes)} villes manquantes exportées → villes_manquantes.txt")
