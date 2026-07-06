"""
Récupération agressive de la DB corrompue.
Pour les pages manquantes, on retourne une fausse page "feuille vide" valide
(type 0x0D, 0 cellules) au lieu de zéros — ce qui évite l'erreur "malformed"
due à un type de page invalide (0x00).
"""
import apsw, sqlite3, struct, os, sys, time

PAGE_SIZE = 4096

# Page "feuille vide" valide : type=0x0D, 0 cellules, content area=PAGE_SIZE
EMPTY_LEAF = (
    b'\x0d'          # type: leaf table b-tree page
    b'\x00\x00'      # first freeblock: none
    b'\x00\x00'      # number of cells: 0
    b'\x10\x00'      # cell content area offset: 4096 (= end of page)
    b'\x00'          # fragmented free bytes: 0
    + b'\x00' * (PAGE_SIZE - 8)
)
assert len(EMPTY_LEAF) == PAGE_SIZE


class PaddedFile(apsw.VFSFile):
    def __init__(self, name, flags, real_size):
        self._real_size = real_size
        super().__init__("", name, flags)

    def xRead(self, amount, offset):
        if offset >= self._real_size:
            # Page manquante → retourner une feuille vide valide
            if amount == PAGE_SIZE and offset % PAGE_SIZE == 0:
                return EMPTY_LEAF[:amount]
            return b'\x00' * amount
        if offset + amount > self._real_size:
            real_data = super().xRead(self._real_size - offset, offset)
            pad_size = amount - len(real_data)
            return real_data + b'\x00' * pad_size
        return super().xRead(amount, offset)

    def xFileSize(self):
        return self._real_size


class PaddedVFS(apsw.VFS):
    def __init__(self, real_size):
        self.real_size = real_size
        super().__init__("padded", "")

    def xOpen(self, name, flags):
        return PaddedFile(name, flags, self.real_size)


def patch_header(db_path, real_pages):
    """Patche le header SQLite pour pointer vers les vraies pages dispo."""
    with open(db_path, 'r+b') as f:
        f.seek(28); f.write(struct.pack('>I', real_pages))
        f.seek(18); f.write(b'\x01\x01')         # format journal legacy
        f.seek(92); f.write(b'\x00\x00\x00\x00') # invalider version_valid_for


def try_read_table(conn, table_name, batch_size=1000):
    """Lit une table en batches par rowid, ignore les erreurs de page."""
    rows = []
    errors = 0
    max_rowid = 0

    # D'abord récupérer le max rowid
    try:
        res = conn.execute(f'SELECT MAX(rowid) FROM "{table_name}"').fetchone()
        max_rowid = res[0] if res and res[0] else 0
    except:
        max_rowid = 10_000_000  # essayer jusqu'à 10M

    if not max_rowid:
        return rows, 0

    start = 1
    while start <= max_rowid:
        end = start + batch_size - 1
        try:
            batch = conn.execute(
                f'SELECT * FROM "{table_name}" WHERE rowid BETWEEN {start} AND {end}'
            ).fetchall()
            rows.extend(batch)
        except Exception as e:
            errors += 1
            # Essayer en sous-batches de 100
            for sub_start in range(start, end + 1, 100):
                sub_end = min(sub_start + 99, end)
                try:
                    batch = conn.execute(
                        f'SELECT * FROM "{table_name}" WHERE rowid BETWEEN {sub_start} AND {sub_end}'
                    ).fetchall()
                    rows.extend(batch)
                except:
                    pass
        start += batch_size

    return rows, errors


def recover(source_db, output_db):
    real_size = os.path.getsize(source_db)
    real_pages = real_size // PAGE_SIZE
    claimed_pages = None

    with open(source_db, 'rb') as f:
        f.seek(28)
        claimed_pages = struct.unpack('>I', f.read(4))[0]

    print(f"Source: {source_db}")
    print(f"  Taille: {real_size//1024//1024}MB, pages réelles: {real_pages}, header: {claimed_pages}")

    patch_header(source_db, real_pages)
    print(f"  Header patché -> {real_pages} pages")

    vfs = PaddedVFS(real_size)

    try:
        src = apsw.Connection(
            f"file:{source_db}?nolock=1",
            flags=apsw.SQLITE_OPEN_READONLY | apsw.SQLITE_OPEN_URI,
            vfs="padded"
        )

        # Lire la structure
        try:
            tables = src.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        except Exception as e:
            print(f"  ERREUR lecture sqlite_master: {e}")
            return

        print(f"  Tables: {[t[0] for t in tables]}")

        # Base de destination
        if os.path.exists(output_db):
            os.remove(output_db)
        dst = sqlite3.connect(output_db)
        dst.execute("PRAGMA journal_mode=WAL")
        dst.execute("PRAGMA synchronous=NORMAL")

        total_rows = 0

        for table_name, create_sql in tables:
            if not create_sql:
                continue
            print(f"\n  → {table_name}")

            # Créer la table dans la destination
            try:
                dst.execute(create_sql)
                dst.commit()
            except Exception as e:
                print(f"    CREATE ERROR: {e}")
                # Essayer de nettoyer le SQL
                try:
                    dst.execute(f"DROP TABLE IF EXISTS \"{table_name}\"")
                    dst.execute(create_sql)
                    dst.commit()
                except Exception as e2:
                    print(f"    Impossible de créer: {e2}")
                    continue

            # Récupérer les noms de colonnes
            try:
                cols_info = src.execute(f'PRAGMA table_info("{table_name}")').fetchall()
                col_names = [c[1] for c in cols_info]
                placeholders = ','.join(['?' for _ in col_names])
            except Exception as e:
                print(f"    PRAGMA ERROR: {e}")
                continue

            # Lire les données
            t0 = time.time()
            rows, errors = try_read_table(src, table_name, batch_size=5000)
            elapsed = time.time() - t0

            print(f"    Récupérées: {len(rows)} lignes, {errors} erreurs batch, {elapsed:.1f}s")

            if rows:
                try:
                    dst.executemany(
                        f'INSERT OR IGNORE INTO "{table_name}" VALUES ({placeholders})',
                        rows
                    )
                    dst.commit()
                    total_rows += len(rows)
                except Exception as e:
                    print(f"    INSERT ERROR: {e}")
                    # Essayer ligne par ligne
                    inserted = 0
                    for row in rows:
                        try:
                            dst.execute(
                                f'INSERT OR IGNORE INTO "{table_name}" VALUES ({placeholders})',
                                row
                            )
                            inserted += 1
                        except:
                            pass
                    dst.commit()
                    print(f"    Ligne par ligne: {inserted}/{len(rows)} insérées")
                    total_rows += inserted

        dst.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        dst.close()
        src.close()

        # Vérification
        print(f"\n✓ Récupération terminée: {total_rows} lignes au total")
        out_size = os.path.getsize(output_db)
        print(f"  Output: {output_db} ({out_size//1024//1024}MB)")

        # Vérif rapide
        check = sqlite3.connect(output_db)
        for row in check.execute("SELECT name, (SELECT COUNT(*) FROM main.\"{}\") FROM sqlite_master WHERE type='table'".replace('"{}"', ''))  .fetchall() if False else []:
            pass
        for tbl in check.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall():
            try:
                n = check.execute(f'SELECT COUNT(*) FROM "{tbl[0]}"').fetchone()[0]
                print(f"  {tbl[0]}: {n} lignes")
            except Exception as e:
                print(f"  {tbl[0]}: ERREUR {e}")
        check.close()

    except Exception as e:
        print(f"Erreur: {e}")
        import traceback; traceback.print_exc()
    finally:
        del vfs


if __name__ == "__main__":
    # Utiliser le backup le plus complet (243MB = 61998 pages)
    source = "/sessions/festive-exciting-faraday/mnt/backend/tenup_backup_20260516_224643.db"
    output = "/sessions/festive-exciting-faraday/mnt/backend/tenup_recovered_v2.db"
    recover(source, output)
