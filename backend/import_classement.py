"""
import_classement.py
────────────────────
Importe les IDs FFT du classement complet (joueurs_padel.csv) dans la scrape_queue.
Ajoute uniquement les joueurs pas encore connus (ni en queue, ni dans joueurs).

Usage :
    python import_classement.py             # import réel
    python import_classement.py --dry-run   # affiche sans modifier
"""
import argparse
import csv
import os
import sqlite3
from datetime import datetime

BASE_DIR = os.path.dirname(__file__)
DB_FILE  = os.path.join(BASE_DIR, 'tenup.db')
CSV_FILE = os.path.join(BASE_DIR, 'joueurs_padel.csv')

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    # Charger les IDs du CSV
    csv_ids = []
    with open(CSV_FILE, encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            id_fft = str(row['idCrm']).strip()
            if id_fft:
                csv_ids.append(id_fft)

    print(f"📄 CSV : {len(csv_ids):,} joueurs")

    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")

    # IDs déjà connus
    queue_ids  = set(r[0] for r in conn.execute("SELECT id_fft FROM scrape_queue").fetchall())
    joueur_ids = set(r[0] for r in conn.execute("SELECT id_fft FROM joueurs").fetchall())
    known_ids  = queue_ids | joueur_ids

    missing = [id_fft for id_fft in csv_ids if id_fft not in known_ids]

    print(f"✅ Déjà en queue/DB : {len(known_ids):,}")
    print(f"➕ À ajouter        : {len(missing):,}")

    if args.dry_run:
        print("⚠️  Mode dry-run — aucune modification")
        conn.close()
        return

    now = datetime.now().isoformat()
    added = 0
    for i, id_fft in enumerate(missing):
        conn.execute(
            "INSERT OR IGNORE INTO scrape_queue (id_fft, statut, added_at) VALUES (?, 'pending', ?)",
            (id_fft, now)
        )
        added += 1
        if (i + 1) % 5000 == 0:
            conn.commit()
            print(f"   {i+1:,}/{len(missing):,} insérés...")

    conn.commit()
    conn.close()

    print(f"\n✅ {added:,} nouveaux joueurs ajoutés à la queue !")

if __name__ == '__main__':
    main()
