"""
Script de nettoyage et normalisation de tenup.db
- Normalise les noms de ville (MAJUSCULES, ST- -> SAINT-, espaces)
- Crée un backup automatique avant toute modification
"""
import sqlite3
import re
import shutil
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "tenup.db")
BACKUP_DIR = os.path.dirname(__file__)


def normalize_ville(v):
    """Normalise un nom de ville : trim, MAJUSCULES, ST-/St-/Saint- → SAINT-."""
    if not v:
        return v
    v = v.strip()
    v = v.upper()
    # ST- ou SAINT- avec tiret → SAINT-
    v = re.sub(r'\bST-', 'SAINT-', v)
    # Espaces multiples
    v = re.sub(r' {2,}', ' ', v)
    return v


def backup_db(db_path, backup_dir):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(backup_dir, f"tenup_clean_backup_{ts}.db")
    shutil.copy2(db_path, backup_path)
    return backup_path


def audit_villes(cur):
    cur.execute("SELECT DISTINCT ville FROM joueurs WHERE ville IS NOT NULL AND ville != ''")
    villes_raw = [r[0] for r in cur.fetchall()]

    mapping = {}
    for v in villes_raw:
        n = normalize_ville(v)
        if n != v:
            mapping[v] = n

    return villes_raw, mapping


def apply_normalization(conn, cur, mapping, dry_run=False):
    """Applique les changements. dry_run=True pour juste compter."""
    total_joueurs = 0
    changes = []
    for old, new in mapping.items():
        cur.execute("SELECT COUNT(*) FROM joueurs WHERE ville = ?", (old,))
        n = cur.fetchone()[0]
        total_joueurs += n
        changes.append((old, new, n))

    if not dry_run:
        for old, new, n in changes:
            cur.execute("UPDATE joueurs SET ville = ? WHERE ville = ?", (new, old))
        conn.commit()

    return changes, total_joueurs


def main(dry_run=False):
    print(f"{'[DRY RUN] ' if dry_run else ''}Nettoyage de {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Audit
    villes_raw, mapping = audit_villes(cur)
    print(f"\nVilles distinctes avant : {len(villes_raw)}")
    print(f"Villes à modifier       : {len(mapping)}")

    # Preview
    changes, total_joueurs = apply_normalization(conn, cur, mapping, dry_run=True)
    print(f"Joueurs affectés        : {total_joueurs}")

    print("\n--- Top 40 transformations (par nb joueurs) ---")
    changes_sorted = sorted(changes, key=lambda x: -x[2])
    for old, new, n in changes_sorted[:40]:
        print(f"  [{n:>5}]  '{old}'  →  '{new}'")

    if not dry_run:
        # Backup
        backup = backup_db(DB_PATH, BACKUP_DIR)
        print(f"\nBackup créé : {os.path.basename(backup)}")

        # Apply
        apply_normalization(conn, cur, mapping, dry_run=False)
        print(f"✓ {len(mapping)} normalisations appliquées sur {total_joueurs} joueurs")

        # Vérification finale
        cur.execute("SELECT COUNT(DISTINCT ville) FROM joueurs WHERE ville IS NOT NULL AND ville != ''")
        print(f"Villes distinctes après : {cur.fetchone()[0]}")
    else:
        print("\n[Mode dry_run — aucune modification en base]")

    conn.close()


if __name__ == "__main__":
    import sys
    dry = "--dry-run" in sys.argv or "-n" in sys.argv
    main(dry_run=dry)
