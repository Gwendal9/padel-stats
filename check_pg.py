import psycopg2, os

conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur = conn.cursor()

cur.execute("""
    SELECT table_name FROM information_schema.tables
    WHERE table_name IN ('classements_historique','cache_responses','tournois_summary')
    ORDER BY table_name
""")
tables = [r[0] for r in cur.fetchall()]
print("Tables existantes sur PG:", tables)

# Vérifier le schéma de classements_historique si elle existe
if "classements_historique" in tables:
    cur.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'classements_historique'
        ORDER BY ordinal_position
    """)
    cols = [r[0] for r in cur.fetchall()]
    print("Colonnes classements_historique:", cols)

# Test rapide profil (TO_DATE issue)
cur.execute("""
    SELECT p.id_joueur,
           SUBSTR(p.date_tournoi, 7, 4) || SUBSTR(p.date_tournoi, 4, 2) || SUBSTR(p.date_tournoi, 1, 2) AS date_sort
    FROM participations p
    LIMIT 5
""")
print("Test SUBSTR dates:", cur.fetchall())

conn.close()
print("OK")
