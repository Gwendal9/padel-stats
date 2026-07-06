"""
Normalisation rapide des villes - approche SQL pour la vitesse.
Etape 1 : UPPER(TRIM(ville)) via SQL
Etape 2 : fix ST- → SAINT- via Python (liste des villes concernées uniquement)
"""
import sqlite3, re

DB = '/sessions/festive-exciting-faraday/mnt/backend/tenup.db'
conn = sqlite3.connect(DB)
cur = conn.cursor()

# Etape 1 : UPPER + TRIM en SQL (une seule requête, très rapide)
cur.execute("""
    UPDATE joueurs
    SET ville = UPPER(TRIM(ville))
    WHERE ville IS NOT NULL AND ville != ''
      AND (ville != UPPER(TRIM(ville)))
""")
n1 = cur.rowcount
print(f"Etape 1 (UPPER/TRIM): {n1} joueurs mis a jour")

# Etape 2 : fix "ST-" → "SAINT-" (seulement les villes qui commencent par ST-)
# On recupere les villes distinctes qui contiennent ST- apres l'etape 1
cur.execute("SELECT DISTINCT ville FROM joueurs WHERE ville LIKE '%ST-%'")
villes_st = [r[0] for r in cur.fetchall()]

updates_st = []
for v in villes_st:
    new_v = re.sub(r'\bST-', 'SAINT-', v)
    if new_v != v:
        updates_st.append((new_v, v))

for new_v, old_v in updates_st:
    cur.execute("UPDATE joueurs SET ville = ? WHERE ville = ?", (new_v, old_v))

n2 = len(updates_st)
print(f"Etape 2 (ST- -> SAINT-): {n2} villes normalisees")

conn.commit()

# Stats finales
cur.execute("SELECT COUNT(DISTINCT ville) FROM joueurs WHERE ville IS NOT NULL AND ville != ''")
print(f"Villes distinctes apres: {cur.fetchone()[0]}")
cur.execute("SELECT COUNT(*) FROM joueurs WHERE ville = 'PARIS'")
print(f"PARIS (verification): {cur.fetchone()[0]}")

conn.close()
print("DONE")
