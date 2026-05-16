"""
init_clubs.py
─────────────
Lit toutes les villes uniques de la base, les croise avec clubs.json,
et affiche celles qui manquent (à remplir manuellement).
Lance : python init_clubs.py
"""
import sqlite3, json, os

DB_FILE    = os.path.join(os.path.dirname(__file__), 'tenup.db')
CLUBS_FILE = os.path.join(os.path.dirname(__file__), 'clubs.json')

# Charger le mapping existant
with open(CLUBS_FILE, encoding='utf-8') as f:
    clubs = json.load(f)
clubs.pop('_note', None)

# Lire les villes de la base
conn = sqlite3.connect(f"file:{DB_FILE}?mode=ro", uri=True)
rows = conn.execute("""
    SELECT ville, COUNT(*) as c
    FROM joueurs
    WHERE ville IS NOT NULL AND ville != ''
    GROUP BY ville
    ORDER BY c DESC
""").fetchall()
conn.close()

print(f"{'='*55}")
print(f"  {len(rows)} villes uniques dans la base")
print(f"{'='*55}\n")

manquantes = [(v, c) for v, c in rows if v not in clubs]
deja_ok    = [(v, c) for v, c in rows if v in clubs]

print(f"✅  {len(deja_ok)} villes déjà mappées\n")

if manquantes:
    print(f"⚠️   {len(manquantes)} villes MANQUANTES à ajouter dans clubs.json :\n")
    print(f"  {'VILLE':<35} {'JOUEURS':>7}")
    print(f"  {'-'*43}")
    for v, c in manquantes:
        print(f"  {v:<35} {c:>7}")

    print(f"\n── Template à copier dans clubs.json ──────────────")
    for v, c in manquantes:
        print(f'  "{v}": {{"dept": "??", "region": "IDF"}},   // {c} joueurs')
else:
    print("🎉  Toutes les villes sont déjà mappées !")

print(f"\n── Répartition par département (villes mappées) ──")
dept_counts = {}
for v, c in rows:
    if v in clubs:
        dept = clubs[v].get('dept', '??')
        dept_counts[dept] = dept_counts.get(dept, 0) + c
for dept, cnt in sorted(dept_counts.items()):
    bar = '█' * (cnt // 5)
    print(f"  {dept}  {cnt:>4} joueurs  {bar}")
