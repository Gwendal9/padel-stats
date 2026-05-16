"""
patch_variations_avril.py — Injecte variation_classement depuis joueurs_padel.csv (avril 2026).

Met UNIQUEMENT à jour variation_classement dans joueurs.
Ne touche pas classement, classement_date, meilleur_classement.
(Ces champs ont déjà été correctement taggués par le backfill.)

Usage : python patch_variations_avril.py
        python patch_variations_avril.py --dry-run
"""
import csv
import os
import sys
import sqlite3
from datetime import datetime

BASE_DIR    = os.path.dirname(__file__)
DB_FILE     = os.path.join(BASE_DIR, 'tenup.db')
CSV_FILE    = os.path.join(BASE_DIR, 'joueurs_padel.csv')
DRY_RUN     = '--dry-run' in sys.argv

def _int(v):
    if v is None or str(v).strip() in ('', 'nan', 'None', 'NaN'):
        return None
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return None

def main():
    print('=== Patch variation_classement (CSV avril 2026) ===')
    if DRY_RUN:
        print('  [DRY RUN — aucune modification]')
    print()

    # ── Charger le CSV ───────────────────────────────────────────────
    rows = []
    with open(CSV_FILE, encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            id_fft = str(r.get('idCrm', '')).strip()
            evol   = _int(r.get('evolution'))
            if id_fft and evol is not None:
                rows.append((evol, id_fft))

    print(f'CSV chargé          : {os.path.basename(CSV_FILE)}')
    print(f'Joueurs avec évol   : {len(rows):,}')

    # Stats rapides
    prog  = sum(1 for v,_ in rows if v < 0)
    desc  = sum(1 for v,_ in rows if v > 0)
    stbl  = sum(1 for v,_ in rows if v == 0)
    print(f'  ▲ Progressions    : {prog:,}  (evolution < 0)')
    print(f'  ▼ Descentes       : {desc:,}  (evolution > 0)')
    print(f'  = Stables         : {stbl:,}  (evolution = 0)')
    print()

    if DRY_RUN:
        print('[DRY RUN] Fin.')
        return

    # ── Update DB ────────────────────────────────────────────────────
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")

    # Vérifier que la colonne existe
    cols = [r[1] for r in conn.execute("PRAGMA table_info(joueurs)").fetchall()]
    if 'variation_classement' not in cols:
        print('  ➕ Ajout colonne variation_classement...')
        conn.execute("ALTER TABLE joueurs ADD COLUMN variation_classement INTEGER")
        conn.commit()

    before_null = conn.execute(
        "SELECT COUNT(*) FROM joueurs WHERE variation_classement IS NULL"
    ).fetchone()[0]
    print(f'Avant : {before_null:,} joueurs sans variation')

    updated = 0
    for i, (evol, id_fft) in enumerate(rows):
        conn.execute(
            "UPDATE joueurs SET variation_classement = ? WHERE id_fft = ?",
            (evol, id_fft)
        )
        updated += 1
        if updated % 20000 == 0:
            conn.commit()
            print(f'  {updated:,}/{len(rows):,}...')

    conn.commit()

    after_null = conn.execute(
        "SELECT COUNT(*) FROM joueurs WHERE variation_classement IS NULL"
    ).fetchone()[0]
    after_filled = conn.execute(
        "SELECT COUNT(*) FROM joueurs WHERE variation_classement IS NOT NULL"
    ).fetchone()[0]

    print(f'\n✅ {updated:,} joueurs mis à jour')
    print(f'Après : {after_filled:,} joueurs avec variation  /  {after_null:,} sans')

    # Mettre à jour aussi classements_historique (snapshot 2026-04)
    hist_updated = conn.execute("""
        UPDATE classements_historique SET variation = j.variation_classement
        FROM joueurs j
        WHERE classements_historique.id_fft = j.id_fft
          AND classements_historique.mois = '2026-04'
          AND j.variation_classement IS NOT NULL
    """)
    # SQLite ne supporte pas UPDATE ... FROM, on le fait autrement
    conn.execute("""
        UPDATE classements_historique
        SET variation = (
            SELECT variation_classement FROM joueurs
            WHERE joueurs.id_fft = classements_historique.id_fft
        )
        WHERE mois = '2026-04'
    """)
    conn.commit()

    hist_filled = conn.execute(
        "SELECT COUNT(*) FROM classements_historique WHERE mois='2026-04' AND variation IS NOT NULL"
    ).fetchone()[0]
    print(f'classements_historique[2026-04] : {hist_filled:,} lignes avec variation')

    conn.close()

    print()
    print('Prochaines étapes :')
    print('  → Redémarre le serveur Flask pour voir les variations dans le leaderboard')
    print('  → Ensuite : python download_classement_csv.py  (CSV mai 2026, H+F)')

if __name__ == '__main__':
    main()
