"""
Corrige les colonnes mal mappées dans tenup_recovered.db,
applique les transformations manquantes, et produit tenup.db final.

Ordre réel des colonnes dans les records B-tree :
  joueurs      : id_fft, nom, prenom, ville, echelon, sexe, naissance,
                 scraped_at, classement, niveau, club_nom, meilleur_classement
  participations : NULL(id), id_joueur, id_tournoi, partenaire_id,
                  partenaire_nom, date_tournoi, position, points, expiration, type
  (les colonnes "stockées" dans la recovered DB ont été insérées dans le mauvais ordre)
"""
import sqlite3, os, shutil, re, time

SRC = "/tmp/tenup_recovered.db"
DST_TMP = "/tmp/tenup_final.db"
DST_FINAL = "/sessions/festive-exciting-faraday/mnt/backend/tenup.db"

t0 = time.time()
if os.path.exists(DST_TMP): os.remove(DST_TMP)
shutil.copy2(SRC, DST_TMP)

c = sqlite3.connect(DST_TMP)
c.execute("PRAGMA foreign_keys=OFF")
c.execute("PRAGMA journal_mode=DELETE")
c.execute("PRAGMA synchronous=NORMAL")

# ─── 1. Corriger joueurs ──────────────────────────────────────────────────────
# Dans la recovered DB, les colonnes sont (par ordre d'insertion erroné) :
#   club_nom=echelon, echelon=sexe, classement=naissance(int),
#   meilleur_classement=scraped_at, sexe=classement(text),
#   naissance=niveau(NULL), niveau=club_nom, scraped_at=meilleur_classement
print("1. Correction joueurs...")
c.executescript("""
CREATE TABLE joueurs_fixed (
    id_fft              TEXT PRIMARY KEY,
    nom                 TEXT,
    prenom              TEXT,
    ville               TEXT,
    echelon             TEXT,
    sexe                TEXT,
    naissance           TEXT,
    scraped_at          TEXT,
    classement          INTEGER,
    niveau              TEXT,
    club_nom            TEXT,
    meilleur_classement INTEGER,
    club_id             INTEGER
);

INSERT OR IGNORE INTO joueurs_fixed
    (id_fft, nom, prenom, ville, echelon, sexe, naissance, scraped_at,
     classement, niveau, club_nom, meilleur_classement)
SELECT
    id_fft, nom, prenom, ville,
    club_nom            AS echelon,
    echelon             AS sexe,
    CAST(classement AS TEXT) AS naissance,
    meilleur_classement AS scraped_at,
    CAST(sexe AS INTEGER)   AS classement,
    naissance           AS niveau,
    niveau              AS club_nom,
    CAST(scraped_at AS INTEGER) AS meilleur_classement
FROM joueurs;

DROP TABLE joueurs;
ALTER TABLE joueurs_fixed RENAME TO joueurs;
""")
c.commit()
n_j = c.execute("SELECT COUNT(*) FROM joueurs").fetchone()[0]
print(f"   joueurs: {n_j:,}")

# Appliquer normalisation ville (UPPER + SAINT-)
print("   Normalisation villes...")
c.execute("""UPDATE joueurs SET ville=UPPER(TRIM(ville))
             WHERE ville IS NOT NULL AND ville != ''""")
c.execute("""UPDATE joueurs SET ville='SAINT-'||SUBSTR(ville,4)
             WHERE ville GLOB 'ST [A-Z]*'""")
c.execute("""UPDATE joueurs SET ville='SAINTE-'||SUBSTR(ville,8)
             WHERE ville GLOB 'SAINTE [A-Z]*'""")
c.execute("""UPDATE joueurs SET ville='SAINT-'||SUBSTR(ville,7)
             WHERE ville GLOB 'SAINT [A-Z]*'""")
c.commit()

# Supprimer colonne niveau (100% NULL — SQLite >= 3.35)
try:
    c.execute("ALTER TABLE joueurs DROP COLUMN niveau")
    print("   colonne niveau supprimée")
except Exception as e:
    print(f"   DROP niveau: {e}")
c.commit()

# ─── 2. Corriger participations ───────────────────────────────────────────────
# Dans la recovered DB :
#   id_joueur=NULL(id), id_tournoi=vrai_id_joueur, partenaire_id=vrai_id_tournoi,
#   partenaire_nom=vrai_partenaire_id, date_tournoi=vrai_partenaire_nom,
#   position=vrai_date_tournoi, points=vrai_position, expiration=vrais_points,
#   type=vrai_expiration, expiration_date=vrai_type
print("2. Correction participations...")
c.executescript("""
CREATE TABLE participations_fixed (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    id_joueur       TEXT,
    id_tournoi      TEXT,
    partenaire_id   TEXT,
    partenaire_nom  TEXT,
    date_tournoi    TEXT,
    position        TEXT,
    points          TEXT,
    expiration      TEXT,
    expiration_date TEXT,
    type            TEXT,
    UNIQUE(id_joueur, id_tournoi)
);

INSERT OR IGNORE INTO participations_fixed
    (id_joueur, id_tournoi, partenaire_id, partenaire_nom,
     date_tournoi, position, points, expiration, type)
SELECT
    id_tournoi      AS id_joueur,
    partenaire_id   AS id_tournoi,
    partenaire_nom  AS partenaire_id,
    date_tournoi    AS partenaire_nom,
    position        AS date_tournoi,
    points          AS position,
    expiration      AS points,
    type            AS expiration,
    expiration_date AS type
FROM participations
WHERE id_tournoi IS NOT NULL;

DROP TABLE participations;
ALTER TABLE participations_fixed RENAME TO participations;
""")
c.commit()
n_p = c.execute("SELECT COUNT(*) FROM participations").fetchone()[0]
print(f"   participations: {n_p:,}")

# ─── 3. Convertir expiration → expiration_date YYYY-MM ───────────────────────
print("3. Conversion expiration → expiration_date...")
MOIS = {
    'janvier':1,'février':2,'mars':3,'avril':4,'mai':5,'juin':6,
    'juillet':7,'août':8,'septembre':9,'octobre':10,'novembre':11,'décembre':12
}
sql_case = "UPDATE participations SET expiration_date = CASE expiration\n"
for mois, num in MOIS.items():
    for yr in [2025, 2026, 2027, 2028]:
        sql_case += f"  WHEN '{mois} {yr}' THEN '{yr}-{num:02d}'\n"
sql_case += "  ELSE NULL END WHERE expiration IS NOT NULL"
c.execute(sql_case)
n_ed = c.execute('SELECT changes()').fetchone()[0]
c.commit()
print(f"   {n_ed:,} lignes mises à jour")

# ─── 4. Créer table clubs ─────────────────────────────────────────────────────
print("4. Création table clubs...")
c.execute("""
    CREATE TABLE IF NOT EXISTS clubs (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        nom        TEXT NOT NULL UNIQUE,
        ville      TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    )
""")
c.execute("""
    INSERT OR IGNORE INTO clubs (nom)
    SELECT DISTINCT club_nom FROM joueurs
    WHERE club_nom IS NOT NULL AND club_nom != ''
""")
n_cl = c.execute('SELECT changes()').fetchone()[0]
c.commit()
print(f"   {n_cl:,} clubs créés")

# ─── 5. Remplir joueurs.club_id ───────────────────────────────────────────────
print("5. Remplissage club_id dans joueurs...")
c.execute("""
    UPDATE joueurs SET club_id = (
        SELECT id FROM clubs WHERE clubs.nom = joueurs.club_nom
    ) WHERE club_nom IS NOT NULL AND club_nom != ''
""")
n_cid = c.execute('SELECT changes()').fetchone()[0]
c.commit()
print(f"   {n_cid:,} joueurs mis à jour")

# ─── 6. Créer joueurs_inactifs (depuis scrape_queue errors) ──────────────────
print("6. Archivage joueurs_inactifs...")
c.execute("""CREATE TABLE IF NOT EXISTS joueurs_inactifs (
    id_fft      TEXT PRIMARY KEY,
    error       TEXT,
    retries     INTEGER,
    added_at    TEXT,
    archived_at TEXT DEFAULT (datetime('now'))
)""")
c.execute("""
    INSERT OR IGNORE INTO joueurs_inactifs (id_fft, error, retries, added_at)
    SELECT id_fft, error, retries, added_at
    FROM scrape_queue WHERE statut = 'error'
""")
n_in = c.execute('SELECT changes()').fetchone()[0]
c.execute("DELETE FROM scrape_queue WHERE statut = 'error'")
c.commit()
print(f"   {n_in:,} joueurs_inactifs archivés")

# ─── 7. Index utiles ──────────────────────────────────────────────────────────
print("7. Création des index...")
indices = [
    "CREATE INDEX IF NOT EXISTS idx_part_joueur  ON participations(id_joueur)",
    "CREATE INDEX IF NOT EXISTS idx_part_tournoi ON participations(id_tournoi)",
    "CREATE INDEX IF NOT EXISTS idx_joueurs_club ON joueurs(club_nom)",
    "CREATE INDEX IF NOT EXISTS idx_joueurs_ville ON joueurs(ville)",
    "CREATE INDEX IF NOT EXISTS idx_joueurs_sexe  ON joueurs(sexe)",
    "CREATE INDEX IF NOT EXISTS idx_sq_statut     ON scrape_queue(statut)",
]
for sql in indices:
    c.execute(sql)
c.commit()
print("   OK")

# ─── 8. Vérification finale ───────────────────────────────────────────────────
print("\n=== Vérification finale ===")
for (tbl,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall():
    n = c.execute(f'SELECT COUNT(*) FROM "{tbl}"').fetchone()[0]
    print(f"  {tbl}: {n:,}")

# Stats joueurs
sexes = c.execute("SELECT sexe, COUNT(*) FROM joueurs GROUP BY sexe ORDER BY sexe").fetchall()
print(f"\n  Répartition sexe: {sexes}")
cities = c.execute("SELECT COUNT(DISTINCT ville) FROM joueurs WHERE ville IS NOT NULL").fetchone()[0]
print(f"  Villes distinctes: {cities:,}")

# Stats participations
types_p = c.execute("SELECT type, COUNT(*) FROM participations GROUP BY type ORDER BY type").fetchall()
print(f"  Types participation: {types_p}")

# Expiration
ed_filled = c.execute("SELECT COUNT(*) FROM participations WHERE expiration_date IS NOT NULL").fetchone()[0]
print(f"  expiration_date rempli: {ed_filled:,}/{n_p:,}")

c.execute("PRAGMA integrity_check(1)")
c.close()

# Copier vers backend
shutil.copy2(DST_TMP, DST_FINAL)
elapsed = time.time() - t0
sz = os.path.getsize(DST_FINAL)
print(f"\n✓ Terminé en {elapsed:.0f}s — {sz//1024//1024}MB → {DST_FINAL}")
