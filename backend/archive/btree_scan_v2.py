"""
Scanner B-tree v2 — avec les vrais schémas du scraper_http.py
Ecrit dans /tmp puis copie vers backend (sqlite ne peut pas créer de WAL sur virtiofs).

Schémas réels :
 joueurs       : id_fft(T) nom(T) prenom(T) ville(T) club_nom(T) echelon(T)
                 classement(I) meilleur_classement(I) sexe(T) naissance(T)
                 niveau(T) scraped_at(T)   → 12 cols, id_fft = TEXT PK (dans record)
 tournois      : id_tournoi(T) nom(T) categorie(T)   → 3 cols, TEXT PK (dans record)
 participations: id(I PK=rowid, PAS dans record), id_joueur(T) id_tournoi(T)
                 partenaire_id(T) partenaire_nom(T) date_tournoi(T)
                 position(T) points(T) expiration(T) type(T) [+expiration_date(T)]
                 → 9 ou 10 valeurs dans le record
 scrape_queue  : id_fft(T) statut(T) added_at(T) processing_at(T) scraped_at(T)
                 error(T) worker_id(T) retries(I)   → 8 cols, TEXT PK (dans record)
 classements_historique : id(I PK=rowid) id_joueur(T) mois(T) classement(I) echelon(T)
                          → 4 valeurs dans record (sans id)
"""
import struct, os, shutil, sqlite3, time, re

PAGE_SIZE  = 4096
SOURCE     = "/sessions/festive-exciting-faraday/mnt/backend/tenup_backup_20260516_224643.db"
TMP_OUT    = "/tmp/tenup_recovered.db"
FINAL_OUT  = "/sessions/festive-exciting-faraday/mnt/backend/tenup_recovered.db"

FFT_RE  = re.compile(r'^\d{5,12}$')
MOIS_RE = re.compile(r'^\d{4}-\d{2}$')

# ── Varint ────────────────────────────────────────────────────────────────────
def read_varint(data, offset):
    result = 0
    for i in range(9):
        if offset + i >= len(data): return 0, 0
        b = data[offset + i]
        if i < 8:
            result = (result << 7) | (b & 0x7F)
            if not (b & 0x80): return result, i + 1
        else:
            result = (result << 8) | b; return result, 9
    return 0, 0

# ── Décodage record ───────────────────────────────────────────────────────────
def decode_record(payload):
    if not payload: return None
    try:
        header_size, consumed = read_varint(payload, 0)
        if header_size <= 0 or header_size > len(payload): return None
        serial_types, pos = [], consumed
        while pos < header_size:
            st, c = read_varint(payload, pos)
            if c == 0: break
            serial_types.append(st); pos += c
        values, pos = [], header_size
        for st in serial_types:
            if   st == 0: values.append(None)
            elif st == 1:
                if pos+1 > len(payload): return None
                values.append(struct.unpack('b', payload[pos:pos+1])[0]); pos += 1
            elif st == 2:
                if pos+2 > len(payload): return None
                values.append(struct.unpack('>h', payload[pos:pos+2])[0]); pos += 2
            elif st == 3:
                if pos+3 > len(payload): return None
                b = payload[pos:pos+3]
                v = struct.unpack('>i', (b'\xff' if b[0]>=128 else b'\x00')+b)[0]
                values.append(v); pos += 3
            elif st == 4:
                if pos+4 > len(payload): return None
                values.append(struct.unpack('>i', payload[pos:pos+4])[0]); pos += 4
            elif st == 5:
                if pos+6 > len(payload): return None
                values.append(struct.unpack('>q', b'\x00\x00'+payload[pos:pos+6])[0]); pos += 6
            elif st == 6:
                if pos+8 > len(payload): return None
                values.append(struct.unpack('>q', payload[pos:pos+8])[0]); pos += 8
            elif st == 7:
                if pos+8 > len(payload): return None
                values.append(struct.unpack('>d', payload[pos:pos+8])[0]); pos += 8
            elif st == 8: values.append(0)
            elif st == 9: values.append(1)
            elif st >= 12 and st % 2 == 0:
                n = (st-12)//2
                if pos+n > len(payload): return None
                values.append(payload[pos:pos+n]); pos += n
            elif st >= 13 and st % 2 == 1:
                n = (st-13)//2
                if pos+n > len(payload): return None
                try:    values.append(payload[pos:pos+n].decode('utf-8', errors='replace'))
                except: values.append(payload[pos:pos+n].decode('latin-1', errors='replace'))
                pos += n
            else: return None
        return values
    except Exception: return None

# ── Parser page feuille ───────────────────────────────────────────────────────
def parse_leaf_page(page_data, is_page1=False):
    offset = 100 if is_page1 else 0
    if len(page_data) < offset+8: return []
    page_type = page_data[offset]
    if page_type != 0x0D: return []
    num_cells = struct.unpack('>H', page_data[offset+3:offset+5])[0]
    if num_cells == 0 or num_cells > 2000: return []
    cell_ptr_start = offset + 8
    rows = []
    for i in range(num_cells):
        ptr_off = cell_ptr_start + i*2
        if ptr_off+2 > len(page_data): break
        cell_ptr = struct.unpack('>H', page_data[ptr_off:ptr_off+2])[0]
        if cell_ptr == 0 or cell_ptr >= PAGE_SIZE: continue
        try:
            payload_len, c1 = read_varint(page_data, cell_ptr)
            if c1 == 0 or payload_len <= 0 or payload_len > 200_000: continue
            rowid, c2 = read_varint(page_data, cell_ptr+c1)
            if c2 == 0: continue
            payload_start = cell_ptr + c1 + c2
            local_size = min(payload_len, PAGE_SIZE - payload_start)
            if local_size <= 0: continue
            record = decode_record(page_data[payload_start:payload_start+local_size])
            if record is not None:
                rows.append((rowid, record))
        except Exception: continue
    return rows

# ── Pages enfants ─────────────────────────────────────────────────────────────
def get_children(page_data, is_page1=False):
    offset = 100 if is_page1 else 0
    if len(page_data) < offset+12: return []
    page_type = page_data[offset]
    if page_type not in (0x02, 0x05): return []
    num_cells = struct.unpack('>H', page_data[offset+3:offset+5])[0]
    children = [struct.unpack('>I', page_data[offset+8:offset+12])[0]]
    for i in range(min(num_cells, 1000)):
        ptr_off = offset+12 + i*2
        if ptr_off+2 > len(page_data): break
        cp = struct.unpack('>H', page_data[ptr_off:ptr_off+2])[0]
        if 0 < cp+4 <= PAGE_SIZE:
            c = struct.unpack('>I', page_data[cp:cp+4])[0]
            if c > 0: children.append(c)
    return children

# ── Classification ────────────────────────────────────────────────────────────
STATUTS = {'pending','done','processing','error','scraped'}

def classify(rows, total_pages):
    if not rows: return None
    sample = [r for _, r in rows[:50]]
    ncols  = max(len(r) for r in sample)

    # Tournois : 3 cols TEXT TEXT TEXT (categorie ~ P\d+ ou Championnat...)
    if ncols == 3:
        cat_match = sum(1 for r in sample if len(r)>=3 and isinstance(r[2], str)
                        and (re.match(r'^P\d+', r[2]) or 'Championnat' in str(r[2]) or 'Épreuve' in str(r[2])))
        if cat_match > 0: return 'tournois'

    # Joueurs : 12 cols, col0 = FFT ID, col1 = nom TEXT (alphanumérique), col8 = sexe H/F
    if ncols >= 10:
        fft0 = sum(1 for r in sample if len(r)>0 and isinstance(r[0], str) and FFT_RE.match(r[0]))
        if fft0 > len(sample)*0.3:
            # scrape_queue aussi : 8 cols, col1 = statut
            statut_match = sum(1 for r in sample if len(r)>1 and r[1] in STATUTS)
            if statut_match > len(sample)*0.3: return 'scrape_queue'
            return 'joueurs'

    # Scrape_queue : 8 cols, col0 = FFT, col1 = statut
    if 6 <= ncols <= 9:
        fft0   = sum(1 for r in sample if len(r)>0 and isinstance(r[0], str) and FFT_RE.match(r[0]))
        statut = sum(1 for r in sample if len(r)>1 and r[1] in STATUTS)
        if fft0 > len(sample)*0.3 and statut > 0: return 'scrape_queue'
        if fft0 > len(sample)*0.3: return 'joueurs_or_scrape'

    # Participations : 9-10 cols (id=rowid pas stocké), col0/col1 = TEXT (joueur/tournoi)
    if 8 <= ncols <= 11:
        # Col3 = type DM/DX/DD ou col (position TEXT)
        type_match = sum(1 for r in sample if len(r)>9 and r[9] in ('DM','DX','DD'))
        fft_col0   = sum(1 for r in sample if len(r)>0 and isinstance(r[0], str) and FFT_RE.match(r[0] or ''))
        fft_col1   = sum(1 for r in sample if len(r)>1 and isinstance(r[1], str) and FFT_RE.match(r[1] or ''))
        if type_match > 0 or (fft_col0 + fft_col1 > len(sample)*0.3):
            return 'participations'

    # classements_historique : 4 cols (rowid exclu), col1 = FFT, col2 = YYYY-MM, col3 = INT
    if 3 <= ncols <= 5:
        mois = sum(1 for r in sample if len(r)>2 and isinstance(r[2], str) and MOIS_RE.match(str(r[2])))
        if mois > len(sample)*0.3: return 'classements_historique'
        fft = sum(1 for r in sample if len(r)>0 and isinstance(r[0], str) and FFT_RE.match(r[0]))
        if fft > len(sample)*0.3 and ncols <= 5: return 'joueurs_inactifs_or_scrape'

    return f'unknown_{ncols}cols'

# ── Traversée B-tree ──────────────────────────────────────────────────────────
def traverse(root, total_pages, f):
    rows, stack, visited = [], [root], set()
    while stack:
        pn = stack.pop()
        if pn in visited or not 1 <= pn <= total_pages: continue
        visited.add(pn)
        f.seek((pn-1)*PAGE_SIZE)
        page = f.read(PAGE_SIZE)
        if len(page) < PAGE_SIZE: continue
        is_p1 = (pn == 1)
        offset = 100 if is_p1 else 0
        pt = page[offset]
        if pt == 0x0D:
            rows.extend(parse_leaf_page(page, is_p1))
        elif pt == 0x05:
            for c in get_children(page, is_p1):
                if c not in visited and 1 <= c <= total_pages:
                    stack.append(c)
    return rows

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    t0 = time.time()
    size = os.path.getsize(SOURCE)
    total_pages = size // PAGE_SIZE
    print(f"Source: {size//1024//1024}MB, {total_pages} pages réelles\n")

    # Passe 1 : graphe de référence
    print("Passe 1 : graphe de pages...")
    referenced, btree = set(), set()
    with open(SOURCE,'rb') as f:
        for pn in range(1, total_pages+1):
            f.seek((pn-1)*PAGE_SIZE)
            p = f.read(PAGE_SIZE)
            if len(p) < PAGE_SIZE: break
            is_p1 = (pn==1)
            off = 100 if is_p1 else 0
            pt = p[off]
            if pt in (0x0D, 0x05, 0x02):
                btree.add(pn)
                if pt in (0x05, 0x02):
                    for c in get_children(p, is_p1):
                        if 1 <= c <= total_pages: referenced.add(c)
            if pn % 10000 == 0: print(f"  {pn}/{total_pages}...")

    roots = sorted((btree - referenced) - {1})
    print(f"  B-tree pages: {len(btree)}, référencées: {len(referenced)}")
    print(f"  Root pages: {roots[:40]}\n")

    # Passe 2 : extraction
    print("Passe 2 : extraction par table...")
    buckets = {}  # table_name -> [(rowid, record)]

    with open(SOURCE,'rb') as f:
        for root in roots:
            rows = traverse(root, total_pages, f)
            if not rows: continue
            label = classify(rows, total_pages)
            print(f"  Page {root:6d}: {len(rows):>8,} lignes → {label}  ex:{rows[0][1][:4]}")
            if label not in buckets: buckets[label] = []
            buckets[label].extend(rows)

    # Passe 3 : écriture
    print("\nPasse 3 : écriture vers DB...")

    SCHEMAS = {
        'joueurs': """CREATE TABLE joueurs (
            id_fft TEXT PRIMARY KEY, nom TEXT, prenom TEXT, ville TEXT,
            club_nom TEXT, echelon TEXT, classement INTEGER,
            meilleur_classement INTEGER, sexe TEXT, naissance TEXT,
            niveau TEXT, scraped_at TEXT
        )""",
        'tournois': """CREATE TABLE tournois (
            id_tournoi TEXT PRIMARY KEY, nom TEXT, categorie TEXT
        )""",
        'participations': """CREATE TABLE participations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_joueur TEXT, id_tournoi TEXT, partenaire_id TEXT,
            partenaire_nom TEXT, date_tournoi TEXT, position TEXT,
            points TEXT, expiration TEXT, type TEXT, expiration_date TEXT
        )""",
        'scrape_queue': """CREATE TABLE scrape_queue (
            id_fft TEXT PRIMARY KEY, statut TEXT, added_at TEXT,
            processing_at TEXT, scraped_at TEXT, error TEXT,
            worker_id TEXT, retries INTEGER
        )""",
        'classements_historique': """CREATE TABLE classements_historique (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_joueur TEXT, mois TEXT, classement INTEGER, echelon TEXT
        )""",
        'clubs': """CREATE TABLE clubs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nom TEXT UNIQUE, ville TEXT, created_at TEXT
        )""",
        'joueurs_inactifs': """CREATE TABLE joueurs_inactifs (
            id_fft TEXT PRIMARY KEY, error TEXT, retries INTEGER,
            added_at TEXT, archived_at TEXT
        )""",
    }

    if os.path.exists(TMP_OUT): os.remove(TMP_OUT)
    dst = sqlite3.connect(TMP_OUT)
    dst.execute("PRAGMA journal_mode=DELETE")
    dst.execute("PRAGMA synchronous=NORMAL")
    dst.execute("PRAGMA foreign_keys=OFF")
    for sql in SCHEMAS.values(): dst.execute(sql)
    dst.commit()

    total_ok = 0

    for label, rows in buckets.items():
        # Mapper label → table + colonnes d'insertion
        if label == 'joueurs':
            tbl = 'joueurs'
            # 12 cols : id_fft, nom, prenom, ville, club_nom, echelon, classement, meilleur_classement, sexe, naissance, niveau, scraped_at
            ins_cols = "id_fft,nom,prenom,ville,club_nom,echelon,classement,meilleur_classement,sexe,naissance,niveau,scraped_at"
            n_ins = 12
        elif label in ('tournois','scrape_queue_or_inactifs','unknown_3cols'):
            # Tournois : 3 cols dans record (id_tournoi, nom, categorie)
            tbl = 'tournois'
            ins_cols = "id_tournoi,nom,categorie"
            n_ins = 3
        elif label == 'participations' or 'unknown_9cols' in label or 'unknown_10cols' in label:
            tbl = 'participations'
            # 9 cols (sans id=rowid) : id_joueur id_tournoi partenaire_id partenaire_nom date_tournoi position points expiration type
            # ou 10 cols si expiration_date présent
            ins_cols = "id_joueur,id_tournoi,partenaire_id,partenaire_nom,date_tournoi,position,points,expiration,type,expiration_date"
            n_ins = 10
        elif label == 'scrape_queue':
            tbl = 'scrape_queue'
            ins_cols = "id_fft,statut,added_at,processing_at,scraped_at,error,worker_id,retries"
            n_ins = 8
        elif label == 'classements_historique':
            tbl = 'classements_historique'
            ins_cols = "id_joueur,mois,classement,echelon"
            n_ins = 4
        elif label in ('joueurs_or_scrape','joueurs_or_scrape_or_inactifs'):
            # Difficile à distinguer — on tente joueurs
            tbl = 'joueurs'
            ins_cols = "id_fft,nom,prenom,ville,club_nom,echelon,classement,meilleur_classement,sexe,naissance,niveau,scraped_at"
            n_ins = 12
        elif label == 'joueurs_inactifs_or_scrape':
            tbl = 'scrape_queue'
            ins_cols = "id_fft,statut,added_at,processing_at,scraped_at,error,worker_id,retries"
            n_ins = 8
        else:
            print(f"  ⚠ Ignoré: {label} ({len(rows):,} lignes)")
            continue

        adapted = []
        for rowid, vals in rows:
            if len(vals) >= n_ins:
                adapted.append(tuple(vals[:n_ins]))
            else:
                adapted.append(tuple(vals) + (None,)*(n_ins - len(vals)))

        ph = ','.join(['?' for _ in range(n_ins)])
        sql = f'INSERT OR IGNORE INTO "{tbl}" ({ins_cols}) VALUES ({ph})'
        try:
            dst.executemany(sql, adapted)
            dst.commit()
            total_ok += len(adapted)
            print(f"  ✓ {tbl}: {len(adapted):,} lignes insérées")
        except Exception as e:
            print(f"  ✗ {tbl} batch: {e}")
            ok = 0
            for row in adapted:
                try: dst.execute(sql, row); ok += 1
                except: pass
            dst.commit()
            print(f"    → ligne/ligne: {ok:,}/{len(adapted):,}")
            total_ok += ok

    dst.execute("PRAGMA integrity_check(1)")
    dst.close()

    # Copie vers backend (virtiofs)
    shutil.copy2(TMP_OUT, FINAL_OUT)

    elapsed = time.time() - t0
    sz = os.path.getsize(FINAL_OUT)
    print(f"\n✓ Terminé en {elapsed:.0f}s — {total_ok:,} lignes — {sz//1024//1024}MB")
    print(f"  Output : {FINAL_OUT}\n")

    chk = sqlite3.connect(FINAL_OUT)
    for (t,) in chk.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall():
        n = chk.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        print(f"  {t}: {n:,}")
    chk.close()

if __name__ == "__main__":
    main()
