import sqlite3

conn = sqlite3.connect('tenup.db', isolation_level=None)

# Ajouter la colonne retries si elle n'existe pas encore
try:
    conn.execute("ALTER TABLE scrape_queue ADD COLUMN retries INTEGER DEFAULT 0")
    conn.commit()
except:
    pass  # existe déjà

n = conn.execute(
    "UPDATE scrape_queue SET statut='pending', retries=0 WHERE statut='done'"
).rowcount
conn.commit()
print(f'{n} joueurs remis en pending')
conn.close()
