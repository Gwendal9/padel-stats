"""
Scanner brute-force SQLite — récupération sans sqlite_master.
1. Scan toutes les pages du backup pour trouver les pages racines des tables
2. Décode les cellules des feuilles (format SQLite record)
3. Reconstruit la DB dans /tmp puis copie vers le backend
"""
import struct, os, shutil, sqlite3, time

PAGE_SIZE = 4096
SOURCE = "/sessions/festive-exciting-faraday/mnt/backend/tenup_backup_20260516_224643.db"
OUTPUT_TMP = "/tmp/tenup_recovered.db"
OUTPUT_FINAL = "/sessions/festive-exciting-faraday/mnt/backend/tenup_recovered.db"


# ── Lecture varint SQLite ──────────────────────────────────────────────────────
def read_varint(data, offset):
    """Retourne (valeur, octets consommés)."""
    result = 0
    for i in range(9):
        if offset + i >= len(data):
            return 0, 0
        b = data[offset + i]
        if i < 8:
            result = (result << 7) | (b & 0x7F)
            if not (b & 0x80):
                return result, i + 1
        else:
            result = (result << 8) | b
            return result, 9
    return 0, 0


# ── Décodage d'un enregistrement SQLite ───────────────────────────────────────
def decode_record(payload):
    """Décode un enregistrement SQLite, retourne une liste de valeurs."""
    if not payload:
        return None
    try:
        header_size, consumed = read_varint(payload, 0)
        if header_size <= 0 or header_size > len(payload):
            return None

        # Lire les serial types
        serial_types = []
        pos = consumed
        while pos < header_size:
            st, c = read_varint(payload, pos)
            if c == 0:
                break
            serial_types.append(st)
            pos += c

        # Décoder les valeurs
        values = []
        pos = header_size
        for st in serial_types:
            if st == 0:
                values.append(None)
            elif st == 1:
                if pos + 1 > len(payload): return None
                values.append(struct.unpack('b', payload[pos:pos+1])[0]); pos += 1
            elif st == 2:
                if pos + 2 > len(payload): return None
                values.append(struct.unpack('>h', payload[pos:pos+2])[0]); pos += 2
            elif st == 3:
                if pos + 3 > len(payload): return None
                b = payload[pos:pos+3]
                v = struct.unpack('>i', b'\x00' + b)[0] if b[0] < 128 else struct.unpack('>i', b'\xff' + b)[0]
                values.append(v); pos += 3
            elif st == 4:
                if pos + 4 > len(payload): return None
                values.append(struct.unpack('>i', payload[pos:pos+4])[0]); pos += 4
            elif st == 5:
                if pos + 6 > len(payload): return None
                b = payload[pos:pos+6]
                values.append(struct.unpack('>q', b'\x00\x00' + b)[0]); pos += 6
            elif st == 6:
                if pos + 8 > len(payload): return None
                values.append(struct.unpack('>q', payload[pos:pos+8])[0]); pos += 8
            elif st == 7:
                if pos + 8 > len(payload): return None
                values.append(struct.unpack('>d', payload[pos:pos+8])[0]); pos += 8
            elif st == 8:
                values.append(0)
            elif st == 9:
                values.append(1)
            elif st >= 12 and st % 2 == 0:
                length = (st - 12) // 2
                if pos + length > len(payload): return None
                values.append(payload[pos:pos+length]); pos += length
            elif st >= 13 and st % 2 == 1:
                length = (st - 13) // 2
                if pos + length > len(payload): return None
                try:
                    values.append(payload[pos:pos+length].decode('utf-8', errors='replace'))
                except:
                    values.append(payload[pos:pos+length].decode('latin-1', errors='replace'))
                pos += length
            else:
                return None
        return values
    except Exception:
        return None


# ── Parser d'une page feuille ─────────────────────────────────────────────────
def parse_leaf_page(page_data, is_page1=False):
    """Extrait toutes les lignes d'une page feuille de table B-tree."""
    offset = 100 if is_page1 else 0
    if len(page_data) < offset + 8:
        return []

    page_type = page_data[offset]
    if page_type != 0x0D:  # Pas une page feuille table
        return []

    num_cells = struct.unpack('>H', page_data[offset+3:offset+5])[0]
    if num_cells == 0 or num_cells > 1000:
        return []

    cell_ptr_start = offset + 8
    rows = []

    for i in range(num_cells):
        ptr_offset = cell_ptr_start + i * 2
        if ptr_offset + 2 > len(page_data):
            break
        cell_ptr = struct.unpack('>H', page_data[ptr_offset:ptr_offset+2])[0]
        if cell_ptr == 0 or cell_ptr >= PAGE_SIZE:
            continue

        try:
            # Lire payload_length (varint)
            payload_len, c1 = read_varint(page_data, cell_ptr)
            if c1 == 0 or payload_len <= 0 or payload_len > 100_000:
                continue

            # Lire rowid (varint)
            rowid, c2 = read_varint(page_data, cell_ptr + c1)
            if c2 == 0:
                continue

            # Lire le payload (peut déborder sur des pages overflow — on prend ce qu'on a)
            payload_start = cell_ptr + c1 + c2
            # Calcul de la portion locale (sans overflow)
            usable_size = PAGE_SIZE  # simplified: pas de réserve par page
            local_payload_max = usable_size - 35  # approximation SQLite

            local_size = min(payload_len, PAGE_SIZE - payload_start)
            if local_size <= 0:
                continue

            payload = page_data[payload_start:payload_start + local_size]
            record = decode_record(payload)
            if record is not None:
                rows.append((rowid, record))
        except Exception:
            continue

    return rows


# ── Identifier les pages internes et leurs enfants ────────────────────────────
def get_children(page_data, is_page1=False):
    """Retourne les pages enfants référencées par une page intérieure."""
    offset = 100 if is_page1 else 0
    if len(page_data) < offset + 8:
        return []

    page_type = page_data[offset]
    if page_type not in (0x02, 0x05):  # Interior index / interior table
        return []

    children = []
    num_cells = struct.unpack('>H', page_data[offset+3:offset+5])[0]

    # Rightmost pointer
    rightmost = struct.unpack('>I', page_data[offset+8:offset+12])[0]
    children.append(rightmost)

    cell_ptr_start = offset + 12
    for i in range(min(num_cells, 500)):
        ptr_offset = cell_ptr_start + i * 2
        if ptr_offset + 2 > len(page_data):
            break
        cell_ptr = struct.unpack('>H', page_data[ptr_offset:ptr_offset+2])[0]
        if cell_ptr > 0 and cell_ptr + 4 <= PAGE_SIZE:
            child = struct.unpack('>I', page_data[cell_ptr:cell_ptr+4])[0]
            if child > 0:
                children.append(child)

    return children


# ── Heuristiques de classification des tables ─────────────────────────────────
import re

FFT_RE = re.compile(r'^\d{5,12}$')

def classify_rows(sample_rows):
    """Devine la table d'origine à partir d'un échantillon de lignes."""
    if not sample_rows:
        return None

    # Prendre jusqu'à 20 lignes
    rows = [r for _, r in sample_rows[:20]]
    ncols = max(len(r) for r in rows)

    # joueurs: ~10 cols, col0 = TEXT FFT ID, col1 = TEXT nom
    if ncols >= 8:
        fft_matches = sum(1 for r in rows if len(r) > 0 and isinstance(r[0], str) and FFT_RE.match(r[0]))
        if fft_matches > len(rows) * 0.5:
            # Vérifier col1 = nom (TEXT), col7 = sexe (H/F)
            sexe_matches = sum(1 for r in rows if len(r) > 7 and r[7] in ('H', 'F', 'M'))
            if sexe_matches > 0:
                return 'joueurs'
            return 'joueurs'  # Probable quand même

    # participations: ~7-8 cols, col0=INTEGER, col1=TEXT FFT ID, col4=TEXT (DM/DX/DD)
    if ncols >= 6:
        int_first = sum(1 for r in rows if len(r) > 0 and isinstance(r[0], int))
        fft_second = sum(1 for r in rows if len(r) > 1 and isinstance(r[1], str) and FFT_RE.match(str(r[1])))
        type_match = sum(1 for r in rows if len(r) > 3 and r[3] in ('DM', 'DX', 'DD'))
        if int_first > len(rows) * 0.5 and (fft_second > 0 or type_match > 0):
            return 'participations'

    # classements_historique: ~5 cols, col0=INT, col1=TEXT FFT, col2=TEXT YYYY-MM, col3=INT classement
    if ncols >= 4:
        mois_match = sum(1 for r in rows if len(r) > 2 and isinstance(r[2], str) and re.match(r'^\d{4}-\d{2}$', str(r[2])))
        if mois_match > len(rows) * 0.3:
            return 'classements_historique'

    # tournois: ~7 cols, col0=TEXT (ID tournoi), col2=TEXT (P25/P100/etc)
    if ncols >= 5:
        cat_match = sum(1 for r in rows if len(r) > 2 and isinstance(r[2], str) and re.match(r'^P\d+', str(r[2])))
        if cat_match > 0:
            return 'tournois'

    # scrape_queue / joueurs_inactifs / clubs
    if ncols <= 3:
        fft_first = sum(1 for r in rows if len(r) > 0 and isinstance(r[0], str) and FFT_RE.match(r[0]))
        if fft_first > len(rows) * 0.5:
            return 'scrape_queue_or_inactifs'

    if ncols == 2 or ncols == 3:
        text_first = sum(1 for r in rows if len(r) > 0 and isinstance(r[0], str))
        if text_first > len(rows) * 0.7:
            return 'clubs_or_small'

    return f'unknown_{ncols}cols'


# ── Scan principal ─────────────────────────────────────────────────────────────
def scan_and_recover():
    t_start = time.time()
    file_size = os.path.getsize(SOURCE)
    total_pages = file_size // PAGE_SIZE
    print(f"Scanning {SOURCE}")
    print(f"  {file_size//1024//1024}MB, {total_pages} pages\n")

    # Première passe: construire le graphe de référence
    print("Passe 1: construction du graphe de pages...")
    referenced_pages = set()  # Pages citées comme enfants
    btree_pages = set()       # Pages qui sont des B-trees valides
    leaf_pages = set()        # Pages feuilles avec données

    with open(SOURCE, 'rb') as f:
        for page_num in range(1, total_pages + 1):
            f.seek((page_num - 1) * PAGE_SIZE)
            page = f.read(PAGE_SIZE)
            if len(page) < PAGE_SIZE:
                break

            is_p1 = (page_num == 1)
            offset = 100 if is_p1 else 0

            if len(page) < offset + 8:
                continue

            page_type = page[offset]

            if page_type in (0x02, 0x05):  # Interior pages
                btree_pages.add(page_num)
                children = get_children(page, is_p1)
                for c in children:
                    if 1 <= c <= total_pages:
                        referenced_pages.add(c)
            elif page_type == 0x0D:  # Leaf table page
                btree_pages.add(page_num)
                leaf_pages.add(page_num)

            if page_num % 10000 == 0:
                print(f"  {page_num}/{total_pages} pages scannées...")

    print(f"  B-tree pages: {len(btree_pages)}, feuilles: {len(leaf_pages)}, référencées: {len(referenced_pages)}")

    # Root pages = B-tree pages non référencées par d'autres (sauf page 1 = sqlite_master)
    root_pages = btree_pages - referenced_pages
    root_pages.discard(1)  # Page 1 = sqlite_master
    print(f"  Root pages candidates: {sorted(root_pages)[:30]}")

    # Deuxième passe: pour chaque root, traverser le B-tree et extraire les données
    print("\nPasse 2: extraction des données par table...")

    table_data = {}  # table_name -> list of rows

    def traverse_btree(root_page_num, f):
        """Traversée récursive du B-tree, retourne toutes les lignes."""
        rows = []
        stack = [root_page_num]
        visited = set()

        while stack:
            pn = stack.pop()
            if pn in visited or pn < 1 or pn > total_pages:
                continue
            visited.add(pn)

            f.seek((pn - 1) * PAGE_SIZE)
            page = f.read(PAGE_SIZE)
            if len(page) < PAGE_SIZE:
                continue

            is_p1 = (pn == 1)
            offset = 100 if is_p1 else 0
            page_type = page[offset]

            if page_type == 0x0D:  # Feuille
                page_rows = parse_leaf_page(page, is_p1)
                rows.extend(page_rows)
            elif page_type == 0x05:  # Intérieure
                children = get_children(page, is_p1)
                for c in children:
                    if c not in visited and 1 <= c <= total_pages:
                        stack.append(c)

        return rows

    with open(SOURCE, 'rb') as f:
        for root in sorted(root_pages):
            rows = traverse_btree(root, f)
            if not rows:
                continue

            table_guess = classify_rows(rows)
            print(f"  Page {root}: {len(rows)} lignes → {table_guess} (ex: {rows[0][1][:3] if rows[0][1] else '?'})")

            if table_guess not in table_data:
                table_data[table_guess] = []
            table_data[table_guess].extend(rows)

    # Troisième passe: écrire dans la DB de sortie
    print("\nPasse 3: écriture dans la DB de sortie...")

    SCHEMAS = {
        'joueurs': """CREATE TABLE joueurs (
            id_fft TEXT PRIMARY KEY, nom TEXT, prenom TEXT, ville TEXT,
            echelon TEXT, naissance TEXT, classement TEXT, sexe TEXT,
            club_nom TEXT, club_id INTEGER, created_at TEXT, updated_at TEXT
        )""",
        'participations': """CREATE TABLE participations (
            id INTEGER PRIMARY KEY, id_joueur TEXT, id_tournoi TEXT,
            type TEXT, position INTEGER, points INTEGER,
            expiration TEXT, expiration_date TEXT
        )""",
        'tournois': """CREATE TABLE tournois (
            id TEXT PRIMARY KEY, nom TEXT, categorie TEXT,
            date_debut TEXT, date_fin TEXT, ville TEXT, surface TEXT
        )""",
        'classements_historique': """CREATE TABLE classements_historique (
            id INTEGER PRIMARY KEY, id_joueur TEXT, mois TEXT,
            classement INTEGER, echelon TEXT
        )""",
        'scrape_queue': """CREATE TABLE scrape_queue (
            id_fft TEXT PRIMARY KEY, statut TEXT, error TEXT,
            retries INTEGER, added_at TEXT, updated_at TEXT
        )""",
        'clubs': """CREATE TABLE clubs (
            id INTEGER PRIMARY KEY, nom TEXT, ville TEXT, created_at TEXT
        )""",
        'joueurs_inactifs': """CREATE TABLE joueurs_inactifs (
            id_fft TEXT PRIMARY KEY, error TEXT, retries INTEGER,
            added_at TEXT, archived_at TEXT
        )""",
    }

    if os.path.exists(OUTPUT_TMP):
        os.remove(OUTPUT_TMP)
    dst = sqlite3.connect(OUTPUT_TMP)
    dst.execute("PRAGMA journal_mode=DELETE")
    dst.execute("PRAGMA synchronous=NORMAL")
    dst.execute("PRAGMA foreign_keys=OFF")

    # Créer toutes les tables
    for schema in SCHEMAS.values():
        dst.execute(schema)
    dst.commit()

    total_inserted = 0
    for table_key, rows in table_data.items():
        # Mapper les clés devinées aux vraies tables
        real_table = None
        if table_key == 'joueurs': real_table = 'joueurs'
        elif table_key == 'participations': real_table = 'participations'
        elif table_key == 'classements_historique': real_table = 'classements_historique'
        elif table_key == 'tournois': real_table = 'tournois'
        elif table_key == 'scrape_queue_or_inactifs': real_table = 'scrape_queue'
        elif table_key == 'clubs_or_small': real_table = 'clubs'

        if real_table is None:
            print(f"  ⚠ Ignoré: {table_key} ({len(rows)} lignes)")
            continue

        cols_info = dst.execute(f'PRAGMA table_info("{real_table}")').fetchall()
        n_cols = len(cols_info)
        placeholders = ','.join(['?' for _ in range(n_cols)])

        adapted = []
        for rowid, vals in rows:
            if len(vals) >= n_cols:
                adapted.append(vals[:n_cols])
            else:
                adapted.append(vals + [None] * (n_cols - len(vals)))

        try:
            dst.executemany(
                f'INSERT OR REPLACE INTO "{real_table}" VALUES ({placeholders})',
                adapted
            )
            dst.commit()
            total_inserted += len(adapted)
            print(f"  ✓ {real_table}: {len(adapted)} lignes insérées")
        except Exception as e:
            print(f"  ✗ {real_table}: {e}")
            # Ligne par ligne
            ok = 0
            for row in adapted:
                try:
                    dst.execute(f'INSERT OR IGNORE INTO "{real_table}" VALUES ({placeholders})', row)
                    ok += 1
                except:
                    pass
            dst.commit()
            print(f"    → ligne par ligne: {ok}/{len(adapted)}")
            total_inserted += ok

    dst.execute("PRAGMA integrity_check(1)")
    dst.close()

    # Copier vers le backend
    shutil.copy2(OUTPUT_TMP, OUTPUT_FINAL)

    elapsed = time.time() - t_start
    out_size = os.path.getsize(OUTPUT_FINAL)
    print(f"\n✓ Terminé en {elapsed:.0f}s — {total_inserted} lignes — {out_size//1024//1024}MB")
    print(f"  → {OUTPUT_FINAL}")

    # Vérification finale
    check = sqlite3.connect(OUTPUT_FINAL)
    for (tbl,) in check.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall():
        n = check.execute(f'SELECT COUNT(*) FROM "{tbl}"').fetchone()[0]
        print(f"  {tbl}: {n:,} lignes")
    check.close()


if __name__ == "__main__":
    scan_and_recover()
