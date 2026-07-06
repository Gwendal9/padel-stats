"""
Normalise les villes avec ST/SAINT :
  ST X    → SAINT-X
  SAINT X → SAINT-X  (seulement quand c'est le début du nom de ville)
"""
import sqlite3, re

DB = '/sessions/festive-exciting-faraday/mnt/backend/tenup.db'
conn = sqlite3.connect(DB)
cur = conn.cursor()

cur.execute("SELECT DISTINCT ville FROM joueurs WHERE ville IS NOT NULL AND ville != ''")
villes = [r[0] for r in cur.fetchall()]

def normalize_saint(v):
    # ST suivi d'un espace → SAINT-
    v = re.sub(r'\bST ', 'SAINT-', v)
    # SAINTE suivi d'un espace → SAINTE-
    v = re.sub(r'\bSAINTE ', 'SAINTE-', v)
    # SAINT suivi d'un espace (et suivi d'autre chose que tiret) → SAINT-
    # mais seulement si le mot suivant commence par une majuscule
    v = re.sub(r'\bSAINT ([A-ZÀÂÄÉÈÊËÏÎÔÙÛÜŸŒÆ])', r'SAINT-\1', v)
    return v

mapping = {}
for v in villes:
    n = normalize_saint(v)
    if n != v:
        mapping[v] = n

print(f"Villes a modifier: {len(mapping)}")

# Apercu
for old, new in list(mapping.items())[:30]:
    cur.execute("SELECT COUNT(*) FROM joueurs WHERE ville = ?", (old,))
    n = cur.fetchone()[0]
    print(f"  [{n:>4}]  '{old}'  ->  '{new}'")

# Application
for old, new in mapping.items():
    cur.execute("UPDATE joueurs SET ville = ? WHERE ville = ?", (new, old))
conn.commit()

print(f"\nApplique: {len(mapping)} villes modifiees")

# Stats
cur.execute("SELECT COUNT(DISTINCT ville) FROM joueurs WHERE ville IS NOT NULL AND ville != ''")
print(f"Villes distinctes apres: {cur.fetchone()[0]}")
conn.close()
print("DONE")
