"""
backfill_april_snapshot.py — Migration one-shot : flag toutes les données
actuelles avec le mois de scrape réel (YYYY-MM extrait de scraped_at).

Ce script :
  1. Applique les migrations de schéma (variation_classement, classement_date,
     table classements_historique) si pas encore fait
  2. Met classement_date = SUBSTR(scraped_at, 1, 7) pour chaque joueur scrapé
     (ex : joueurs scrapés en avril → '2026-04', en mai → '2026-05')
     Fallback '2026-04' pour les joueurs sans scraped_at (ne devrait pas arriver)
  3. Remplit classements_historique avec un snapshot PAR MOIS de scrape
  4. Affiche un résumé pour vérification

À lancer UNE SEULE FOIS avant le premier refresh mensuel de mai.

Usage : python backfill_april_snapshot.py
        python backfill_april_snapshot.py --dry-run   # vérification sans écriture
"""
import os
import sys
import sqlite3
from datetime import datetime

DB_FILE  = os.path.join(os.path.dirname(__file__), 'tenup.db')
DRY_RUN  = '--dry-run' in sys.argv
FALLBACK_MOIS = '2026-04'  # mois fallback si scraped_at est NULL

def main():
    print(f'=== Backfill snapshots par mois de scrape ===')
    if DRY_RUN:
        print('  [DRY RUN — aucune modification]')
    print()

    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")

    # ── Étape 1 : migrations de schéma ──────────────────────────────
    print('Étape 1 : migrations de schéma...')
    migrations = [
        "ALTER TABLE joueurs ADD COLUMN variation_classement INTEGER",
        "ALTER TABLE joueurs ADD COLUMN classement_date TEXT",
        """CREATE TABLE IF NOT EXISTS classements_historique (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            id_fft      TEXT NOT NULL,
            mois        TEXT NOT NULL,
            classement  INTEGER,
            variation   INTEGER,
            meilleur_classement INTEGER,
            scraped_at  TEXT,
            UNIQUE(id_fft, mois)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_hist_joueur ON classements_historique(id_fft)",
        "CREATE INDEX IF NOT EXISTS idx_hist_mois   ON classements_historique(mois)",
    ]
    for m in migrations:
        try:
            conn.execute(m)
            conn.commit()
            short = m.strip().split('\n')[0][:80]
            print(f'  ✅ {short}')
        except sqlite3.OperationalError as e:
            if 'already exists' in str(e) or 'duplicate column' in str(e):
                pass  # déjà migré
            else:
                print(f'  ⚠️  {e}')

    # ── Étape 2 : stats avant modification ─────────────────────────
    print()
    total_j     = conn.execute("SELECT COUNT(*) FROM joueurs").fetchone()[0]
    avec_cl     = conn.execute("SELECT COUNT(*) FROM joueurs WHERE classement IS NOT NULL").fetchone()[0]
    h_count     = conn.execute("SELECT COUNT(*) FROM joueurs WHERE sexe='H' AND classement IS NOT NULL").fetchone()[0]
    f_count     = conn.execute("SELECT COUNT(*) FROM joueurs WHERE sexe='F' AND classement IS NOT NULL").fetchone()[0]
    deja_tagges = conn.execute("SELECT COUNT(*) FROM joueurs WHERE classement_date IS NOT NULL").fetchone()[0]
    hist_count  = conn.execute("SELECT COUNT(*) FROM classements_historique").fetchone()[0]

    print(f'Joueurs total       : {total_j:,}')
    print(f'Avec classement     : {avec_cl:,}  (H: {h_count:,}  F: {f_count:,})')
    print(f'Déjà taggués        : {deja_tagges:,}')
    print(f'Lignes historique   : {hist_count:,} lignes existantes')

    # Distribution par mois de scrape
    print()
    print('Répartition par mois de scrape (scraped_at) :')
    mois_distrib = conn.execute("""
        SELECT SUBSTR(scraped_at, 1, 7) as mois_scrape, COUNT(*) as n
        FROM joueurs
        WHERE scraped_at IS NOT NULL AND classement IS NOT NULL
        GROUP BY 1 ORDER BY 1
    """).fetchall()
    for r in mois_distrib:
        print(f'   {r[0]}  →  {r[1]:,} joueurs classés')

    # Distributions actuelles
    print()
    print('Distribution classement H (top tranches) :')
    for r in conn.execute("""
        SELECT
          CASE WHEN classement<=100 THEN 'Top 100'
               WHEN classement<=1000 THEN 'Top 1000'
               WHEN classement<=5000 THEN 'Top 5000'
               WHEN classement<=20000 THEN 'Top 20000'
               ELSE '>20000' END as tr,
          COUNT(*) as n
        FROM joueurs WHERE sexe='H' AND classement IS NOT NULL
        GROUP BY 1 ORDER BY MIN(classement)
    """):
        print(f'   {r[0]:<12} : {r[1]:>6,}')

    print()
    print('Distribution classement F (top tranches) :')
    for r in conn.execute("""
        SELECT
          CASE WHEN classement<=100 THEN 'Top 100'
               WHEN classement<=1000 THEN 'Top 1000'
               WHEN classement<=5000 THEN 'Top 5000'
               WHEN classement<=20000 THEN 'Top 20000'
               ELSE '>20000' END as tr,
          COUNT(*) as n
        FROM joueurs WHERE sexe='F' AND classement IS NOT NULL
        GROUP BY 1 ORDER BY MIN(classement)
    """):
        print(f'   {r[0]:<12} : {r[1]:>6,}')

    # Plage de dates de scrape
    dates = conn.execute("""
        SELECT MIN(scraped_at), MAX(scraped_at), COUNT(DISTINCT SUBSTR(scraped_at,1,7))
        FROM joueurs WHERE scraped_at IS NOT NULL
    """).fetchone()
    print()
    print(f'Scrape range : {dates[0][:10] if dates[0] else "?"} → {dates[1][:10] if dates[1] else "?"}')
    print(f'Mois de scrape distincts : {dates[2]}')

    # Nb participations et date range
    p_range = conn.execute("""
        SELECT COUNT(*), MIN(date_tournoi), MAX(date_tournoi)
        FROM participations
    """).fetchone()
    print(f'Participations : {p_range[0]:,}  ({p_range[1]} → {p_range[2]})')

    # Nb joueurs actifs récemment (participations post 2026-01-01)
    actifs_2026 = conn.execute("""
        SELECT COUNT(DISTINCT p.id_joueur)
        FROM participations p
        WHERE SUBSTR(p.date_tournoi,7,4)||SUBSTR(p.date_tournoi,4,2)||SUBSTR(p.date_tournoi,1,2) >= '20260101'
    """).fetchone()[0]
    actifs_3m = conn.execute("""
        SELECT COUNT(DISTINCT p.id_joueur)
        FROM participations p
        WHERE SUBSTR(p.date_tournoi,7,4)||SUBSTR(p.date_tournoi,4,2)||SUBSTR(p.date_tournoi,1,2) >= '20260201'
    """).fetchone()[0]
    print(f'Joueurs avec tournoi depuis jan 2026 : {actifs_2026:,}')
    print(f'Joueurs avec tournoi depuis fev 2026 : {actifs_3m:,}')

    if DRY_RUN:
        print('\n[DRY RUN] Fin — aucune modification.')
        conn.close()
        return

    # ── Étape 3 : tag classement_date par mois de scraped_at ───────
    print(f'\nÉtape 3 : tag classement_date = mois de scraped_at...')
    # Joueurs avec scraped_at : on prend les 7 premiers caractères (YYYY-MM)
    conn.execute("""
        UPDATE joueurs
        SET classement_date = SUBSTR(scraped_at, 1, 7)
        WHERE classement_date IS NULL
          AND scraped_at IS NOT NULL
    """)
    # Fallback pour les rares joueurs sans scraped_at
    conn.execute("""
        UPDATE joueurs
        SET classement_date = ?
        WHERE classement_date IS NULL
    """, (FALLBACK_MOIS,))
    conn.commit()

    # Résumé du tagging
    tag_rows = conn.execute("""
        SELECT classement_date, COUNT(*) as n
        FROM joueurs
        WHERE classement_date IS NOT NULL
        GROUP BY 1 ORDER BY 1
    """).fetchall()
    for r in tag_rows:
        print(f'  ✅ {r[1]:,} joueurs taggués {r[0]}')

    # ── Étape 4 : backfill classements_historique (un snapshot/mois) ─
    print(f'\nÉtape 4 : insertion snapshots dans classements_historique...')

    # Récupérer la liste des mois présents
    mois_list = [r[0] for r in conn.execute("""
        SELECT DISTINCT classement_date FROM joueurs
        WHERE classement_date IS NOT NULL AND classement IS NOT NULL
        ORDER BY 1
    """).fetchall()]

    total_inserted = 0
    for mois in mois_list:
        conn.execute("""
            INSERT INTO classements_historique
                (id_fft, mois, classement, variation, meilleur_classement, scraped_at)
            SELECT id_fft, classement_date, classement, variation_classement,
                   meilleur_classement, scraped_at
            FROM joueurs
            WHERE classement_date = ?
              AND classement IS NOT NULL
            ON CONFLICT(id_fft, mois) DO NOTHING
        """, (mois,))
        n = conn.execute(
            "SELECT COUNT(*) FROM classements_historique WHERE mois=?", (mois,)
        ).fetchone()[0]
        print(f'  ✅ {n:,} lignes dans classements_historique[{mois}]')
        total_inserted += n

    conn.commit()

    # ── Résumé final ───────────────────────────────────────────────
    print()
    print('=== Résumé final ===')
    for mois in mois_list:
        n = conn.execute(
            "SELECT COUNT(*) FROM classements_historique WHERE mois=?", (mois,)
        ).fetchone()[0]
        print(f'  Snapshot {mois} : {n:,} joueurs')
    print(f'  variation_classement : NULL pour l\'instant (sera rempli au prochain scrape)')
    print()
    print('Prochaines étapes :')
    print('  1. Lancer probe_rang_fields.py pour trouver le champ "evolutionRang"')
    print('  2. En juin (après le 1er mardi) : python monthly_refresh.py --smart')
    print('     → Scraper seulement les joueurs actifs (~50-70k) en priorité')
    print('     → python scraper_http.py --workers 15')

    conn.close()

if __name__ == '__main__':
    main()
