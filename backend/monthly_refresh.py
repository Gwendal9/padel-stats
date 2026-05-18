"""
monthly_refresh.py — Rafraîchissement mensuel de la base de données.

La FFT publie les nouveaux classements le 1er mardi de chaque mois.
Ce script remet les joueurs en queue pour re-scrape selon 3 modes :

  --smart   (recommandé) : seulement les joueurs actifs récemment.
            Tier 1 : participations dans les 3 derniers mois  → scrapés en premier
            Tier 2 : participations dans les 12 derniers mois → scrapés ensuite
            Tier 3 : inactifs > 12 mois                       → scrapés en dernier (ou ignorés)
            Résultat : ~50-70k joueurs au lieu de 156k → 3-5h au lieu de 10-12h.

  (défaut)  : remet TOUS les joueurs en pending (scrape complet)
  --force   : force même si le mois a déjà été scrapé
  --check   : vérifie l'état sans modifier

Puis lancer le scraper :
  python scraper_http.py --workers 15      ← recommandé (HTTP pur, 10x plus rapide)
  python scraper_fast.py --workers 6       ← fallback Playwright
"""
import os
import sys
import sqlite3
from datetime import datetime

DB_FILE = os.path.join(os.path.dirname(__file__), 'tenup.db')

FORCE  = '--force' in sys.argv
CHECK  = '--check' in sys.argv
SMART  = '--smart' in sys.argv

def main():
    mois = datetime.now().strftime('%Y-%m')

    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")

    print(f'=== Monthly Refresh — {mois} ===\n')

    # ── Stats actuelles ──────────────────────────────────────────────
    total_j   = conn.execute("SELECT COUNT(*) FROM joueurs").fetchone()[0]
    avec_cl   = conn.execute("SELECT COUNT(*) FROM joueurs WHERE classement IS NOT NULL").fetchone()[0]
    avec_var  = conn.execute("SELECT COUNT(*) FROM joueurs WHERE variation_classement IS NOT NULL").fetchone()[0] if _col_exists(conn, 'joueurs', 'variation_classement') else 0
    total_q   = conn.execute("SELECT COUNT(*) FROM scrape_queue").fetchone()[0]
    done_q    = conn.execute("SELECT COUNT(*) FROM scrape_queue WHERE statut='done'").fetchone()[0]
    pending_q = conn.execute("SELECT COUNT(*) FROM scrape_queue WHERE statut='pending'").fetchone()[0]
    parts     = conn.execute("SELECT COUNT(*) FROM participations").fetchone()[0]

    # Date du dernier scrape
    last_scrape = conn.execute(
        "SELECT MAX(scraped_at) FROM joueurs WHERE scraped_at IS NOT NULL"
    ).fetchone()[0] or 'jamais'

    print(f'Joueurs     : {total_j:>8,}  (classés: {avec_cl:,}, avec variation: {avec_var:,})')
    print(f'Queue       : {total_q:>8,}  (done: {done_q:,}, pending: {pending_q:,})')
    print(f'Participations: {parts:>6,}')
    print(f'Dernier scrape : {last_scrape[:16]}')

    # ── Historique des mois scrapés ──────────────────────────────────
    if _table_exists(conn, 'classements_historique'):
        print('\n── Snapshots historique ──')
        rows = conn.execute("""
            SELECT mois, COUNT(*) as n, AVG(classement) as avg_cl
            FROM classements_historique
            GROUP BY mois ORDER BY mois DESC
        """).fetchall()
        for r in rows:
            print(f'  {r[0]}  →  {r[1]:,} joueurs  moy #{r[2]:.0f}')
        if not rows:
            print('  (vide — premier refresh en attente)')
    else:
        print('\n⚠️  Table classements_historique absente. Lancer le serveur Flask pour créer le schéma.')

    if CHECK:
        print('\n[--check] Aucune modification effectuée.')
        conn.close()
        return

    # ── Vérification doublon mois ────────────────────────────────────
    if not FORCE and _table_exists(conn, 'classements_historique'):
        n_this_month = conn.execute(
            "SELECT COUNT(*) FROM classements_historique WHERE mois = ?", (mois,)
        ).fetchone()[0]
        if n_this_month > 1000:
            print(f'\n⚠️  Un snapshot existe déjà pour {mois} ({n_this_month:,} joueurs).')
            print('   Utilise --force pour relancer quand même.')
            conn.close()
            return

    # ── Remise en pending ────────────────────────────────────────────
    # Calculer la date limite pour "actifs" (3 mois glissants)
    from datetime import timedelta
    today = datetime.now()
    cutoff_3m  = (today - timedelta(days=90)).strftime('%Y%m%d')
    cutoff_12m = (today - timedelta(days=365)).strftime('%Y%m%d')

    if SMART:
        # ── Mode SMART : priorité aux joueurs actifs ─────────────────
        print('\n📋 Mode --smart : analyse de l\'activité récente...')

        # Joueurs actifs Tier 1 : tournoi dans les 3 derniers mois
        t1 = conn.execute(f"""
            SELECT COUNT(DISTINCT id_joueur) FROM participations
            WHERE SUBSTR(date_tournoi,7,4)||SUBSTR(date_tournoi,4,2)||SUBSTR(date_tournoi,1,2) >= '{cutoff_3m}'
        """).fetchone()[0]

        # Joueurs actifs Tier 2 : tournoi dans les 12 derniers mois (pas déjà dans T1)
        t2 = conn.execute(f"""
            SELECT COUNT(DISTINCT id_joueur) FROM participations
            WHERE SUBSTR(date_tournoi,7,4)||SUBSTR(date_tournoi,4,2)||SUBSTR(date_tournoi,1,2) >= '{cutoff_12m}'
              AND SUBSTR(date_tournoi,7,4)||SUBSTR(date_tournoi,4,2)||SUBSTR(date_tournoi,1,2) < '{cutoff_3m}'
        """).fetchone()[0]

        # Tier 3 : inactifs
        t3 = total_j - t1 - t2
        print(f'   Tier 1 (actifs <3 mois)  : {t1:,} joueurs  ← scrapés en premier')
        print(f'   Tier 2 (actifs <12 mois) : {t2:,} joueurs  ← scrapés ensuite')
        print(f'   Tier 3 (inactifs)         : {t3:,} joueurs  ← scrapés en dernier')
        print()

        # Remettre en "done" uniquement les joueurs déjà scrapés (scraped_at IS NOT NULL).
        # Les pending jamais scrapés (scraped_at IS NULL) restent en pending —
        # les convertir en done les ferait disparaître de joueurs sans jamais être traités.
        conn.execute("""
            UPDATE scrape_queue SET statut='done'
            WHERE statut IN ('pending','processing','error')
              AND scraped_at IS NOT NULL
        """)

        # Puis remettre pending par priorité via ajout d'une colonne priority
        # (on utilise le champ added_at pour trier : les + récents = prioritaires)
        now_iso = datetime.now().isoformat()

        # Tier 1 en pending avec added_at = maintenant (priorité haute)
        conn.execute(f"""
            UPDATE scrape_queue SET
                statut='pending', processing_at=NULL, worker_id=NULL,
                error=NULL, retries=0, added_at='{now_iso}'
            WHERE id_fft IN (
                SELECT DISTINCT id_joueur FROM participations
                WHERE SUBSTR(date_tournoi,7,4)||SUBSTR(date_tournoi,4,2)||SUBSTR(date_tournoi,1,2) >= '{cutoff_3m}'
            )
        """)

        # Tier 2 en pending avec added_at = 1 jour avant (priorité moyenne)
        d2 = (today - timedelta(days=1)).isoformat()
        conn.execute(f"""
            UPDATE scrape_queue SET
                statut='pending', processing_at=NULL, worker_id=NULL,
                error=NULL, retries=0, added_at='{d2}'
            WHERE statut='done'
            AND id_fft IN (
                SELECT DISTINCT id_joueur FROM participations
                WHERE SUBSTR(date_tournoi,7,4)||SUBSTR(date_tournoi,4,2)||SUBSTR(date_tournoi,1,2) >= '{cutoff_12m}'
            )
        """)

        # Tier 3 : on laisse en 'done' pour l'instant (seront traités si le scraper continue)
        # Ou décommente pour les inclure aussi avec basse priorité :
        # d3 = (today - timedelta(days=30)).isoformat()
        # conn.execute(f"UPDATE scrape_queue SET statut='pending', added_at='{d3}' WHERE statut='done'")

    else:
        # ── Mode complet : tous les joueurs ──────────────────────────
        print(f'\n📋 Mode complet : remise en pending de {done_q:,} joueurs...')
        conn.execute("""
            UPDATE scrape_queue
            SET statut = 'pending', processing_at = NULL, worker_id = NULL,
                error = NULL, retries = 0
            WHERE statut = 'done'
        """)

    # Aussi remettre les 'error' (avec peu de retries) en pending
    conn.execute("""
        UPDATE scrape_queue
        SET statut = 'pending', processing_at = NULL, worker_id = NULL,
            error = NULL, retries = 0
        WHERE statut = 'error' AND retries < 3
    """)
    conn.commit()

    new_pending = conn.execute(
        "SELECT COUNT(*) FROM scrape_queue WHERE statut='pending'"
    ).fetchone()[0]
    mode_str = 'smart (actifs en priorité)' if SMART else 'complet'
    print(f'✅ {new_pending:,} joueurs prêts pour le scrape ({mode_str}).')

    # Estimation temps
    workers = 15
    delay_avg = 2.25   # moyenne DELAY_MIN/DELAY_MAX du scraper HTTP
    secs = new_pending / workers * delay_avg
    heures = secs / 3600
    print(f'\n⏱️  Estimation avec scraper_http.py --workers {workers} :')
    print(f'   ~{heures:.1f} heures ({heures*60:.0f} minutes)')

    print(f'\nLance maintenant :')
    print(f'  python scraper_http.py --workers 15   ← recommandé (HTTP, 10x plus rapide)')
    print(f'  python scraper_fast.py --workers 6    ← fallback Playwright')
    print(f'\nAvancement :')
    print(f'  python check_db.py')

    conn.close()


def _col_exists(conn, table, col):
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    return col in cols


def _table_exists(conn, table):
    n = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()[0]
    return n > 0


if __name__ == '__main__':
    main()
