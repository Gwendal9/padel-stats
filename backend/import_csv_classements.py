"""
import_csv_classements.py — Import rapide des classements FFT depuis le CSV officiel.

Le CSV exporté depuis tenup.fft.fr/classements/padel contient déjà :
  classement, evolution (variation mensuelle), meilleurClassement, nom, prenom, club…

Ce script :
  1. Met à jour classement / variation_classement / meilleur_classement pour tous
     les joueurs déjà en DB (correspondance par idCrm = id_fft)
  2. Ajoute les nouveaux joueurs (pas encore en DB) à la scrape_queue
  3. Insère un snapshot dans classements_historique pour le mois courant
  4. Affiche un résumé

C'est 1000x plus rapide qu'un scrape complet pour avoir les classements du mois.
Le scrape complet (scraper_http.py) reste nécessaire pour les participations.

Usage :
    python import_csv_classements.py joueurs_padel.csv
    python import_csv_classements.py joueurs_padel_H.csv joueurs_padel_F.csv
    python import_csv_classements.py --dry-run joueurs_padel.csv
"""
import os
import sys
import csv
import sqlite3
from datetime import datetime

DB_FILE  = os.path.join(os.path.dirname(__file__), 'tenup.db')
DRY_RUN  = '--dry-run' in sys.argv
csv_args = [a for a in sys.argv[1:] if not a.startswith('--')]

if not csv_args:
    # Cherche automatiquement les CSV dans le dossier courant
    csv_args = [f for f in os.listdir(os.path.dirname(__file__) or '.')
                if f.lower().startswith('joueurs_padel') and f.endswith('.csv')]
    if not csv_args:
        print("Usage : python import_csv_classements.py joueurs_padel.csv [joueurs_padel_F.csv ...]")
        sys.exit(1)
    print(f"📂 CSV trouvés automatiquement : {csv_args}")

MOIS_COURANT = datetime.now().strftime('%Y-%m')

def load_csv(path: str) -> list[dict]:
    full = os.path.join(os.path.dirname(__file__), path) if not os.path.isabs(path) else path
    if not os.path.exists(full):
        print(f"❌ Fichier introuvable : {full}")
        return []
    rows = []
    with open(full, encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            id_fft = str(r.get('idCrm', '')).strip()
            if not id_fft:
                continue
            rows.append({
                'id_fft':       id_fft,
                'nom':          r.get('nom', '').strip(),
                'prenom':       r.get('prenom', '').strip(),
                'club_nom':     r.get('club', '').strip(),
                'classement':   _int(r.get('classement')),
                'variation':    _int(r.get('evolution')),   # négatif = progression (FFT convention)
                'meilleur':     _int(r.get('meilleurClassement')),
                'ligue':        r.get('ligue', '').strip(),
                'comite':       r.get('comite', '').strip(),
            })
    print(f"  📄 {os.path.basename(path)} → {len(rows):,} joueurs")
    return rows


def _int(v):
    """Convertit en entier (gère '', 'nan', '0.0', None)."""
    if v is None or str(v).strip() in ('', 'nan', 'None', 'NaN'):
        return None
    try:
        f = float(v)
        return int(f) if f == f else None   # NaN check
    except (ValueError, TypeError):
        return None


def main():
    print(f'=== Import CSV classements — {MOIS_COURANT} ===')
    if DRY_RUN:
        print('  [DRY RUN — aucune modification]')
    print()

    # ── Charger tous les CSV ─────────────────────────────────────────
    all_rows = []
    for path in csv_args:
        all_rows.extend(load_csv(path))

    if not all_rows:
        print("❌ Aucune donnée à importer.")
        sys.exit(1)

    # Dédoublonner sur id_fft (garder le dernier)
    by_id = {}
    for r in all_rows:
        by_id[r['id_fft']] = r
    all_rows = list(by_id.values())
    print(f'Total joueurs uniques : {len(all_rows):,}')

    # ── Connexion DB ─────────────────────────────────────────────────
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")

    # ── Stats avant ──────────────────────────────────────────────────
    db_ids     = set(r[0] for r in conn.execute("SELECT id_fft FROM joueurs").fetchall())
    queue_ids  = set(r[0] for r in conn.execute("SELECT id_fft FROM scrape_queue").fetchall())

    csv_ids    = {r['id_fft'] for r in all_rows}
    to_update  = [r for r in all_rows if r['id_fft'] in db_ids]
    to_add     = [r for r in all_rows if r['id_fft'] not in db_ids and r['id_fft'] not in queue_ids]

    print(f'Joueurs en DB           : {len(db_ids):,}')
    print(f'Joueurs dans CSV        : {len(csv_ids):,}')
    print(f'  → À mettre à jour     : {len(to_update):,}  (déjà en DB)')
    print(f'  → Nouveaux à ajouter  : {len(to_add):,}  (ni DB ni queue)')
    print(f'  → En DB mais pas CSV  : {len(db_ids - csv_ids):,}  (partenaires découverts, conservés)')
    print()

    # Aperçu des variations
    vars_non_null = [r for r in to_update if r['variation'] is not None]
    vars_prog     = sum(1 for r in vars_non_null if r['variation'] < 0)
    vars_desc     = sum(1 for r in vars_non_null if r['variation'] > 0)
    vars_stable   = sum(1 for r in vars_non_null if r['variation'] == 0)
    print(f'Variations dans CSV     : {len(vars_non_null):,}')
    print(f'  ▲ Progressions (<0)   : {vars_prog:,}')
    print(f'  ▼ Descentes   (>0)    : {vars_desc:,}')
    print(f'  = Stable      (=0)    : {vars_stable:,}')
    print()

    if DRY_RUN:
        print('[DRY RUN] Fin — aucune modification.')
        conn.close()
        return

    now = datetime.now().isoformat()

    # ── Étape 1 : mise à jour classements dans joueurs ───────────────
    print('Étape 1 : mise à jour classements / variation / meilleur...')
    updated = 0
    for r in to_update:
        conn.execute("""
            UPDATE joueurs SET
                classement             = COALESCE(?, classement),
                variation_classement   = ?,
                meilleur_classement    = COALESCE(?, meilleur_classement),
                classement_date        = ?
            WHERE id_fft = ?
        """, (r['classement'], r['variation'], r['meilleur'], MOIS_COURANT, r['id_fft']))
        updated += 1
        if updated % 10000 == 0:
            conn.commit()
            print(f'   {updated:,}/{len(to_update):,}...')
    conn.commit()
    print(f'  ✅ {updated:,} joueurs mis à jour')

    # ── Étape 2 : ajout des nouveaux joueurs à la queue ──────────────
    print(f'\nÉtape 2 : ajout de {len(to_add):,} nouveaux joueurs à la queue...')
    added = 0
    for r in to_add:
        conn.execute(
            "INSERT OR IGNORE INTO scrape_queue (id_fft, statut, added_at) VALUES (?, 'pending', ?)",
            (r['id_fft'], now)
        )
        added += 1
        if added % 5000 == 0:
            conn.commit()
            print(f'   {added:,}/{len(to_add):,}...')
    conn.commit()
    print(f'  ✅ {added:,} nouveaux joueurs en queue')

    # ── Étape 3 : snapshot classements_historique ────────────────────
    print(f'\nÉtape 3 : snapshot {MOIS_COURANT} dans classements_historique...')
    # Vérifie si la table existe
    tbl = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='classements_historique'"
    ).fetchone()[0]
    if not tbl:
        conn.execute("""
            CREATE TABLE classements_historique (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                id_fft      TEXT NOT NULL,
                mois        TEXT NOT NULL,
                classement  INTEGER,
                variation   INTEGER,
                meilleur_classement INTEGER,
                scraped_at  TEXT,
                UNIQUE(id_fft, mois)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_hist_joueur ON classements_historique(id_fft)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_hist_mois   ON classements_historique(mois)")
        conn.commit()

    # Existing snapshot count for this month
    existing = conn.execute(
        "SELECT COUNT(*) FROM classements_historique WHERE mois=?", (MOIS_COURANT,)
    ).fetchone()[0]

    if existing > 1000:
        print(f'  ⚠️  Snapshot {MOIS_COURANT} déjà présent ({existing:,} lignes) — remplacement...')
        conn.execute("DELETE FROM classements_historique WHERE mois=?", (MOIS_COURANT,))
        conn.commit()

    conn.execute("""
        INSERT INTO classements_historique (id_fft, mois, classement, variation, meilleur_classement, scraped_at)
        SELECT id_fft, classement_date, classement, variation_classement, meilleur_classement, scraped_at
        FROM joueurs
        WHERE classement_date = ?
          AND classement IS NOT NULL
        ON CONFLICT(id_fft, mois) DO UPDATE SET
            classement  = excluded.classement,
            variation   = excluded.variation,
            meilleur_classement = excluded.meilleur_classement
    """, (MOIS_COURANT,))
    conn.commit()

    snap = conn.execute(
        "SELECT COUNT(*) FROM classements_historique WHERE mois=?", (MOIS_COURANT,)
    ).fetchone()[0]
    print(f'  ✅ {snap:,} lignes dans classements_historique[{MOIS_COURANT}]')

    # ── Résumé historique ────────────────────────────────────────────
    print()
    print('── Snapshots disponibles ──')
    for row in conn.execute("""
        SELECT mois, COUNT(*) as n, AVG(classement) as avg_cl
        FROM classements_historique
        GROUP BY mois ORDER BY mois
    """).fetchall():
        print(f'  {row[0]}  →  {row[1]:,} joueurs  moy #{row[2]:.0f}')

    print()
    print('✅ Import terminé !')
    print()
    print('Prochaines étapes :')
    print('  → Relancer le serveur Flask pour voir les classements mis à jour :')
    print('     python dashboard/api.py')
    print()
    print('  → Pour mettre à jour les participations (tournois) :')
    print('     python monthly_refresh.py          ← remet tous les joueurs en pending')
    print('     python scraper_http.py --workers 15 ← ~6-12h scrape complet')

    conn.close()


if __name__ == '__main__':
    main()
