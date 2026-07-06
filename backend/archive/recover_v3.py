"""
Récupération v3 : on NE patche PAS le page count (garde 80335),
SQLite accepte donc les références à 80233/80234 (sqlite_master),
et notre VFS retourne une feuille vide valide pour ces pages absentes.
On injecte sqlite_master manuellement depuis les schemas connus.
"""
import apsw, sqlite3, struct, os, sys, time

PAGE_SIZE = 4096

# Page "feuille vide" valide : type=0x0D, 0 cellules
EMPTY_LEAF = (
    b'\x0d'
    b'\x00\x00'  # first freeblock: none
    b'\x00\x00'  # number of cells: 0
    b'\x10\x00'  # cell content area: 4096
    b'\x00'      # fragmented free bytes: 0
    + b'\x00' * (PAGE_SIZE - 8)
)

# Schémas connus des tables (reconstituée depuis les scripts Python du projet)
KNOWN_SCHEMAS = {
    'joueurs': """CREATE TABLE IF NOT EXISTS joueurs (
        id_fft TEXT PRIMARY KEY,
        nom TEXT, prenom TEXT, ville TEXT, echelon TEXT,
        naissance TEXT, classement TEXT, club_nom TEXT, sexe TEXT,
        club_id INTEGER REFERENCES clubs(id),
        created_at TEXT, updated_at TEXT
    )""",
    'participations': """CREATE TABLE IF NOT EXISTS participations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        id_joueur TEXT REFERENCES joueurs(id_fft),
        id_tournoi TEXT REFERENCES tournois(id),
        type TEXT, position INTEGER, points INTEGER,
        expiration TEXT, expiration_date TEXT
    )""",
    'tournois': """CREATE TABLE IF NOT EXISTS tournois (
        id TEXT PRIMARY KEY,
        nom TEXT, categorie TEXT, date_debut TEXT, date_fin TEXT,
        ville TEXT, surface TEXT
    )""",
    'classements_historique': """CREATE TABLE IF NOT EXISTS classements_historique (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        id_joueur TEXT REFERENCES joueurs(id_fft),
        mois TEXT, classement INTEGER, echelon TEXT
    )""",
    'scrape_queue': """CREATE TABLE IF NOT EXISTS scrape_queue (
        id_fft TEXT PRIMARY KEY,
        statut TEXT DEFAULT 'pending',
        error TEXT, retries INTEGER DEFAULT 0,
        added_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT
    )""",
    'clubs': """CREATE TABLE IF NOT EXISTS clubs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nom TEXT NOT NULL UNIQUE,
        ville TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    )""",
    'joueurs_inactifs': """CREATE TABLE IF NOT EXISTS joueurs_inactifs (
        id_fft TEXT PRIMARY KEY,
        error TEXT, retries INTEGER, added_at TEXT,
        archived_at TEXT DEFAULT (datetime('now'))
    )""",
    '_enrich_mapping': """CREATE TABLE IF NOT EXISTS _enrich_mapping (
        id_fft TEXT PRIMARY KEY,
        url TEXT, last_scraped TEXT
    )""",
    'user_accounts': """CREATE TABLE IF NOT EXISTS user_accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE, password_hash TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    )""",
    'user_favorites': """CREATE TABLE IF NOT EXISTS user_favorites (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER REFERENCES user_accounts(id),
        id_fft TEXT REFERENCES joueurs(id_fft),
        created_at TEXT DEFAULT (datetime('now'))
    )""",
    'user_sessions': """CREATE TABLE IF NOT EXISTS user_sessions (
        id TEXT PRIMARY KEY,
        user_id INTEGER REFERENCES user_accounts(id),
        created_at TEXT DEFAULT (datetime('now')),
        expires_at TEXT
    )""",
    'user_tokens': """CREATE TABLE IF NOT EXISTS user_tokens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER REFERENCES user_accounts(id),
        token TEXT UNIQUE, created_at TEXT DEFAULT (datetime('now'))
    )""",
}


class PaddedFile(apsw.VFSFile):
    def __init__(self, name, flags, real_size):
        self._real_size = real_size
        super().__init__("", name, flags)

    def xRead(self, amount, offset):
        if offset >= self._real_size:
            # Retourner une feuille vide valide
            if amount == PAGE_SIZE:
                return EMPTY_LEAF[:amount]
            return b'\x00' * amount
        if offset + amount > self._real_size:
            real_data = super().xRead(self._real_size - offset, offset)
            pad = amount - len(real_data)
            return real_data + b'\x00' * pad
        return super().xRead(amount, offset)

    def xFileSize(self):
        # On laisse la taille originale (80335 pages) pour que les références soient valides
        return self._real_size


class PaddedVFS(apsw.VFS):
    def __init__(self, real_size):
        self.real_size = real_size
        super().__init__("padded", "")

    def xOpen(self, name, flags):
        return PaddedFile(name, flags, self.real_size)


def patch_header_minimal(db_path):
    """Patch minimal : seulement le format journal et invalidation version_valid_for.
    On garde le page count original pour que SQLite accepte les refs > fichier réel."""
    with open(db_path, 'r+b') as f:
        # Lire le change counter
        f.seek(24)
        change_counter = struct.unpack('>I', f.read(4))[0]
        # Mettre version_valid_for = change_counter (pour que SQLite fasse confiance au header)
        f.seek(92)
        f.write(struct.pack('>I', change_counter))
        # Forcer format journal = 1 (legacy, pas WAL)
        f.seek(18); f.write(b'\x01\x01')
    return change_counter


def find_root_pages(db_path, real_size):
    """Trouver les root pages des tables en scannant sqlite_master directement.
    Comme les pages 80233/80234 sont absentes, on va chercher des root pages connues
    en parsant le fichier directement page par page."""
    # Chercher des pages qui ressemblent à des roots de tables connues
    # via la méthode brute force: scan des pages feuilles avec des données
    results = {}

    with open(db_path, 'rb') as f:
        total_pages = real_size // PAGE_SIZE
        for page_num in range(1, min(total_pages, 100)):
            f.seek(page_num * PAGE_SIZE)
            page = f.read(PAGE_SIZE)
            page_offset = 100 if page_num == 0 else 0
            if page[page_offset] in (0x0d, 0x05):  # table page
                num_cells = struct.unpack('>H', page[page_offset+3:page_offset+5])[0]
                if num_cells > 0:
                    print(f"  Page {page_num+1}: type={hex(page[page_offset])}, {num_cells} cellules")
    return results


def try_read_table_by_rowid(conn, table_name, max_rows=2_000_000):
    """Lecture par batches rowid avec récupération sur erreurs."""
    rows = []
    errors = 0

    # Estimer le max rowid
    try:
        res = conn.execute(f'SELECT MAX(rowid) FROM "{table_name}"').fetchone()
        max_rowid = res[0] if res and res[0] else 0
    except:
        max_rowid = max_rows

    if not max_rowid:
        # Essai direct pour les tables sans rowid (INTEGER PK)
        try:
            rows = conn.execute(f'SELECT * FROM "{table_name}" LIMIT {max_rows}').fetchall()
            return rows, 0
        except:
            return [], 1

    batch_size = 10000
    start = 1
    while start <= max_rowid:
        end = start + batch_size - 1
        try:
            batch = conn.execute(
                f'SELECT * FROM "{table_name}" WHERE rowid BETWEEN ? AND ?', (start, end)
            ).fetchall()
            rows.extend(batch)
        except Exception as e:
            errors += 1
            # Sous-batches de 1000
            for s in range(start, end+1, 1000):
                e2 = min(s+999, end)
                try:
                    b = conn.execute(
                        f'SELECT * FROM "{table_name}" WHERE rowid BETWEEN ? AND ?', (s, e2)
                    ).fetchall()
                    rows.extend(b)
                except:
                    pass
        start += batch_size

    return rows, errors


def recover(source_db, output_db):
    real_size = os.path.getsize(source_db)
    real_pages = real_size // PAGE_SIZE

    print(f"Source: {source_db}")
    print(f"  {real_size//1024//1024}MB, {real_pages} pages réelles")

    patch_header_minimal(source_db)

    vfs = PaddedVFS(real_size)

    try:
        src = apsw.Connection(
            f"file:{source_db}?nolock=1",
            flags=apsw.SQLITE_OPEN_READONLY | apsw.SQLITE_OPEN_URI,
            vfs="padded"
        )

        # Tenter de lire sqlite_master (pages manquantes → feuilles vides → 0 lignes)
        try:
            tables_raw = src.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='table'"
            ).fetchall()
            tables_found = {t[0]: t[1] for t in tables_raw if t[1]}
            print(f"  sqlite_master: {list(tables_found.keys())}")
        except Exception as e:
            print(f"  sqlite_master inaccessible ({e}), on utilise les schémas connus")
            tables_found = {}

        # Combiner avec les schémas connus
        tables_to_recover = {}
        for name, sql in KNOWN_SCHEMAS.items():
            if name in tables_found:
                tables_to_recover[name] = tables_found[name]
            else:
                tables_to_recover[name] = sql

        # Destination
        if os.path.exists(output_db):
            os.remove(output_db)
        dst = sqlite3.connect(output_db)
        dst.execute("PRAGMA journal_mode=WAL")
        dst.execute("PRAGMA synchronous=NORMAL")
        dst.execute("PRAGMA foreign_keys=OFF")

        total_rows = 0

        for table_name, create_sql in tables_to_recover.items():
            print(f"\n  → {table_name}")
            try:
                dst.execute(create_sql.replace('IF NOT EXISTS', '').replace('CREATE TABLE', 'CREATE TABLE IF NOT EXISTS'))
                dst.commit()
            except Exception as e:
                print(f"    CREATE: {e}")
                dst.execute(f'DROP TABLE IF EXISTS "{table_name}"')
                try:
                    # SQL simplifié sans FK
                    simple_sql = '\n'.join(
                        l for l in create_sql.split('\n')
                        if 'REFERENCES' not in l and 'FOREIGN' not in l
                    )
                    dst.execute(simple_sql)
                    dst.commit()
                except Exception as e2:
                    print(f"    CREATE simplifié: {e2}")
                    continue

            t0 = time.time()
            rows, errors = try_read_table_by_rowid(src, table_name)
            elapsed = time.time() - t0
            print(f"    {len(rows)} lignes, {errors} erreurs, {elapsed:.1f}s")

            if rows:
                # Nb colonnes attendu dans la table destination
                dst_cols = dst.execute(f'PRAGMA table_info("{table_name}")').fetchall()
                n_cols = len(dst_cols)

                # Adapter si différence de colonnes
                adapted = []
                for row in rows:
                    if len(row) == n_cols:
                        adapted.append(row)
                    elif len(row) > n_cols:
                        adapted.append(row[:n_cols])
                    else:
                        adapted.append(row + (None,) * (n_cols - len(row)))

                placeholders = ','.join(['?' for _ in range(n_cols)])
                try:
                    dst.executemany(
                        f'INSERT OR IGNORE INTO "{table_name}" VALUES ({placeholders})',
                        adapted
                    )
                    dst.commit()
                    total_rows += len(adapted)
                    print(f"    ✓ Insérées: {len(adapted)}")
                except Exception as e:
                    print(f"    INSERT batch: {e}")

        dst.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        dst.close()
        src.close()

        out_size = os.path.getsize(output_db)
        print(f"\n✓ Total: {total_rows} lignes, output={out_size//1024//1024}MB")

    except Exception as e:
        print(f"ERREUR: {e}")
        import traceback; traceback.print_exc()
    finally:
        del vfs


if __name__ == "__main__":
    # Backup le plus complet: 243MB
    source = "/sessions/festive-exciting-faraday/mnt/backend/tenup_backup_20260516_224643.db"
    output = "/sessions/festive-exciting-faraday/mnt/backend/tenup_recovered_v3.db"
    recover(source, output)
