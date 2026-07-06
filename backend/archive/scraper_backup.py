"""
Tenup Cascade Scraper
─────────────────────
Démarre depuis un joueur seed, scrape son profil,
découvre ses partenaires via l'API REST, les ajoute à la queue, et répète.

Usage :
    python scraper.py              -> limite par défaut (200 joueurs)
    python scraper.py --limit 1000 -> limite custom
    python scraper.py --limit 0    -> illimité (attention !)
"""

import sqlite3
import json
import re
import os
import time
import random
import argparse
import requests
from datetime import datetime
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# Config
TENUP_BASE      = 'https://tenup.fft.fr'
COOKIES_FILE    = os.path.join(os.path.dirname(__file__), 'cookies.json')
DB_FILE         = os.path.join(os.path.dirname(__file__), 'tenup.db')
SEED_ID         = '7633273415'
DELAY_MIN       = 5.0
DELAY_MAX       = 10.0
API_SEARCH_URL  = f'{TENUP_BASE}/back/v1/personnes/joueurs-padel'

# ── Cookies ───────────────────────────────────────────────────────
COOKIE_DOMAINS = {
    'SHARED_SESSION_JAVA':                      'tenup.fft.fr',
    'SSESS7ba44afc36c80c3faa2b8fa87e7742c5':    '.fft.fr',
    'datadome':                                  '.fft.fr',
    'QueueITAccepted-SDFrts345E-V3_tenupprod':  'tenup.fft.fr',
}

def load_cookies():
    if not os.path.exists(COOKIES_FILE):
        raise FileNotFoundError("cookies.json introuvable")
    with open(COOKIES_FILE) as f:
        data = json.load(f)
    cookies = []
    for k, v in data.items():
        if v and not v.startswith('COLLE_'):
            domain = COOKIE_DOMAINS.get(k, 'tenup.fft.fr')
            cookies.append({'name': k, 'value': v, 'domain': domain, 'path': '/'})
    print(f"✅ {len(cookies)} cookies chargés")
    return cookies

def init_session():
    """Session requests pour l'API REST (recherche joueur)"""
    with open(COOKIES_FILE) as f:
        raw = json.load(f)
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Content-Type': 'application/json',
        'Referer': f'{TENUP_BASE}/recherche/joueurs/padel',
        'Origin': TENUP_BASE,
    })
    for name, value in raw.items():
        if value and not value.startswith('COLLE_'):
            session.cookies.set(name, value, domain='tenup.fft.fr')
    return session

# ── Recherche joueur par nom ──────────────────────────────────────
def parse_partner_name(full_name):
    """
    Découpe "Mathis TAMISIER" → (nom="TAMISIER", prenom="Mathis")
    Les mots tout en MAJUSCULES = nom de famille
    """
    parts = full_name.strip().split()
    nom_parts    = [p for p in parts if p == p.upper() and p.isalpha()]
    prenom_parts = [p for p in parts if p not in nom_parts]

    # Fallback : dernier mot = nom, reste = prénom
    if not nom_parts or not prenom_parts:
        if len(parts) >= 2:
            prenom_parts = parts[:-1]
            nom_parts    = parts[-1:]
        else:
            return None, None

    return ' '.join(nom_parts), ' '.join(prenom_parts)

def search_joueur_padel(session, partenaire_nom):
    """
    Cherche un joueur padel via l'API REST.
    Retourne son idCrm (str) ou None.
    """
    if not partenaire_nom or not partenaire_nom.strip():
        return None

    nom, prenom = parse_partner_name(partenaire_nom)
    if not nom or not prenom:
        return None

    try:
        r = session.post(
            API_SEARCH_URL,
            json={"from": 0, "size": 5, "nom": nom, "prenom": prenom},
            timeout=10
        )
        if r.status_code == 200:
            data = r.json()
            joueurs = data.get('joueurs', [])
            if joueurs:
                return str(joueurs[0]['idCrm'])
        elif r.status_code != 400:
            print(f"   ⚠️  API search HTTP {r.status_code} pour {nom} {prenom}")
    except Exception as e:
        print(f"   ⚠️  API search erreur: {e}")
    return None

# ── Base de données ───────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_FILE)
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS joueurs (
            id_fft      TEXT PRIMARY KEY,
            nom         TEXT,
            prenom      TEXT,
            ville       TEXT,
            echelon     TEXT,
            sexe        TEXT,
            naissance   TEXT,
            classement  INTEGER,
            niveau      TEXT,
            scraped_at  TEXT
        );
        CREATE TABLE IF NOT EXISTS tournois (
            id_tournoi  TEXT PRIMARY KEY,
            nom         TEXT,
            categorie   TEXT
        );
        CREATE TABLE IF NOT EXISTS participations (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            id_joueur       TEXT,
            id_tournoi      TEXT,
            partenaire_id   TEXT,
            partenaire_nom  TEXT,
            date_tournoi    TEXT,
            position        TEXT,
            points          TEXT,
            expiration      TEXT,
            type            TEXT,
            UNIQUE(id_joueur, id_tournoi)
        );
        CREATE TABLE IF NOT EXISTS scrape_queue (
            id_fft      TEXT PRIMARY KEY,
            statut      TEXT DEFAULT 'pending',
            added_at    TEXT,
            scraped_at  TEXT,
            error       TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_queue_statut  ON scrape_queue(statut);
        CREATE INDEX IF NOT EXISTS idx_part_tournoi  ON participations(id_tournoi);
        CREATE INDEX IF NOT EXISTS idx_part_joueur   ON participations(id_joueur);
        CREATE INDEX IF NOT EXISTS idx_part_partner  ON participations(partenaire_id);
    ''')
    # Migration : ajouter colonnes classement/niveau si absentes
    try:
        conn.execute("ALTER TABLE joueurs ADD COLUMN classement INTEGER")
        conn.execute("ALTER TABLE joueurs ADD COLUMN niveau TEXT")
        conn.commit()
    except:
        pass
    conn.commit()
    return conn

def add_to_queue(conn, id_fft):
    conn.execute(
        "INSERT OR IGNORE INTO scrape_queue (id_fft, statut, added_at) VALUES (?, 'pending', ?)",
        (id_fft, datetime.now().isoformat())
    )
    conn.commit()

def get_next(conn):
    row = conn.execute(
        "SELECT id_fft FROM scrape_queue WHERE statut='pending' ORDER BY added_at ASC LIMIT 1"
    ).fetchone()
    return row[0] if row else None

def mark_done(conn, id_fft):
    conn.execute(
        "UPDATE scrape_queue SET statut='done', scraped_at=? WHERE id_fft=?",
        (datetime.now().isoformat(), id_fft)
    )
    conn.commit()

def mark_error(conn, id_fft, error):
    conn.execute(
        "UPDATE scrape_queue SET statut='error', scraped_at=?, error=? WHERE id_fft=?",
        (datetime.now().isoformat(), str(error)[:500], id_fft)
    )
    conn.commit()

def stats(conn):
    total   = conn.execute("SELECT COUNT(*) FROM scrape_queue").fetchone()[0]
    done    = conn.execute("SELECT COUNT(*) FROM scrape_queue WHERE statut='done'").fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM scrape_queue WHERE statut='pending'").fetchone()[0]
    errors  = conn.execute("SELECT COUNT(*) FROM scrape_queue WHERE statut='error'").fetchone()[0]
    joueurs = conn.execute("SELECT COUNT(*) FROM joueurs").fetchone()[0]
    parts   = conn.execute("SELECT COUNT(*) FROM participations").fetchone()[0]
    return total, done, pending, errors, joueurs, parts

# ── Playwright ────────────────────────────────────────────────────
def fetch_profile(browser, cookies, id_fft):
    url = f"{TENUP_BASE}/classement/{id_fft}/padel"
    context = browser.new_context(
        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        locale='fr-FR',
    )
    context.add_cookies(cookies)
    page = context.new_page()
    try:
        page.goto(url, wait_until='domcontentloaded', timeout=60000)

        if 'queue' in page.url.lower():
            print(f"   ⏳ Queue-it détecté... attente 30s")
            time.sleep(30)
            page.wait_for_url(f'**/classement/{id_fft}/**', timeout=120000)

        try:
            page.wait_for_load_state('networkidle', timeout=20000)
        except:
            pass

        try:
            page.wait_for_selector('table', timeout=15000)
        except:
            pass

        time.sleep(1)

        for attempt in range(3):
            try:
                html = page.content()
                break
            except Exception as e:
                if attempt < 2:
                    print(f"   ⚠️  page.content() retry {attempt+1}: {e}")
                    time.sleep(2)
                else:
                    raise
    finally:
        context.close()
    return html

# ── Parser ────────────────────────────────────────────────────────
def parse_profile(html, id_fft):
    profile = {
        'id_fft': id_fft,
        'nom': '', 'prenom': '', 'ville': '',
        'echelon': '', 'sexe': '', 'naissance': '',
        'tournaments': []
    }

    idx = html.find('"fft_fiche_joueur"')
    if idx != -1:
        start = html.find('{', idx)
        depth, end = 0, start
        for i, c in enumerate(html[start:], start):
            if c == '{': depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0: end = i + 1; break
        try:
            fiche = json.loads(html[start:end])
            profile['nom']       = fiche.get('nom', '')
            profile['prenom']    = fiche.get('prenom', '')
            profile['ville']     = fiche.get('ville', '')
            profile['sexe']      = fiche.get('sexe', '')
            profile['naissance'] = fiche.get('birthYear', '')
            if fiche.get('echelon'):
                profile['echelon'] = str(fiche['echelon'])
        except Exception as e:
            print(f"   ⚠️  fft_fiche_joueur parse error: {e}")

    soup = BeautifulSoup(html, 'html.parser')
    for table in soup.select('table'):
        for row in table.select('tbody tr'):
            cells = row.select('td')
            if len(cells) < 5:
                continue
            texts = [c.get_text(strip=True) for c in cells]
            if not re.match(r'\d{2}/\d{2}/\d{4}', texts[0]):
                continue

            id_tournoi, lien = '', ''
            link_el = cells[1].select_one('a[href]') if len(cells) > 1 else None
            if link_el:
                href = link_el.get('href', '')
                lien = f"{TENUP_BASE}{href}" if href.startswith('/') else href
                m = re.search(r'/tournoi/(\d+)', lien)
                if m: id_tournoi = m.group(1)

            partenaire_id = ''
            if len(cells) > 4:
                p_link = cells[4].select_one('a[href]')
                if p_link:
                    pm = re.search(r'/(?:classement|fichejoueur)/(\d+)', p_link.get('href', ''))
                    if pm: partenaire_id = pm.group(1)

            profile['tournaments'].append({
                'id_tournoi':     id_tournoi,
                'nom':            texts[1] if len(texts) > 1 else '',
                'categorie':      texts[2] if len(texts) > 2 else '',
                'type':           texts[3] if len(texts) > 3 else '',
                'partenaire_nom': texts[4] if len(texts) > 4 else '',
                'partenaire_id':  partenaire_id,
                'position':       texts[5] if len(texts) > 5 else '',
                'points':         texts[6] if len(texts) > 6 else '',
                'expiration':     texts[7] if len(texts) > 7 else '',
                'date':           texts[0],
                'lien':           lien,
            })

    return profile

def save_profile(conn, profile):
    conn.execute('''
        INSERT INTO joueurs (id_fft, nom, prenom, ville, echelon, sexe, naissance, scraped_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id_fft) DO UPDATE SET
            nom=excluded.nom, prenom=excluded.prenom,
            ville=excluded.ville, echelon=excluded.echelon,
            sexe=excluded.sexe, naissance=excluded.naissance,
            scraped_at=excluded.scraped_at
    ''', (
        profile['id_fft'], profile['nom'], profile['prenom'],
        profile['ville'], profile['echelon'], profile['sexe'],
        profile['naissance'], datetime.now().isoformat()
    ))

    for t in profile['tournaments']:
        if t['id_tournoi']:
            conn.execute(
                "INSERT OR IGNORE INTO tournois (id_tournoi, nom, categorie) VALUES (?, ?, ?)",
                (t['id_tournoi'], t['nom'], t['categorie'])
            )
        conn.execute('''
            INSERT INTO participations
                (id_joueur, id_tournoi, partenaire_id, partenaire_nom,
                 date_tournoi, position, points, expiration, type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id_joueur, id_tournoi) DO UPDATE SET
                partenaire_id=COALESCE(NULLIF(excluded.partenaire_id,''), participations.partenaire_id)
        ''', (
            profile['id_fft'], t['id_tournoi'], t['partenaire_id'],
            t['partenaire_nom'], t['date'], t['position'],
            t['points'], t['expiration'], t['type']
        ))
    conn.commit()

# ── Boucle principale ─────────────────────────────────────────────
def run(limit=200):
    print(f"🎾 Tenup Cascade Scraper — limite : {limit if limit > 0 else 'illimitée'}")
    print(f"📁 Base : {DB_FILE}")

    cookies = load_cookies()
    session = init_session()
    conn    = init_db()
    add_to_queue(conn, SEED_ID)

    scraped_this_run = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=['--disable-blink-features=AutomationControlled']
        )
        try:
            while True:
                if limit > 0 and scraped_this_run >= limit:
                    print(f"\n✅ Limite de {limit} joueurs atteinte.")
                    break

                id_fft = get_next(conn)
                if not id_fft:
                    print("\n✅ Queue vide — tous les joueurs ont été scrapés !")
                    break

                total, done, pending, errors, joueurs, parts = stats(conn)
                print(f"\n[{done+1}/{total}] 🎭 {id_fft} — "
                      f"pending:{pending} done:{done} erreurs:{errors} "
                      f"| joueurs:{joueurs} participations:{parts}")

                try:
                    html    = fetch_profile(browser, cookies, id_fft)
                    profile = parse_profile(html, id_fft)

                    if profile['nom']:
                        print(f"   👤 {profile['prenom']} {profile['nom']} "
                              f"— {len(profile['tournaments'])} tournois")
                    else:
                        print(f"   ⚠️  Profil vide (queue-it ? session expirée ?)")

                    # ── Résolution des IDs partenaires via API ──────
                    partners_to_add = []
                    for t in profile['tournaments']:
                        if t['partenaire_nom'] and not t['partenaire_id']:
                            pid = search_joueur_padel(session, t['partenaire_nom'])
                            if pid:
                                t['partenaire_id'] = pid
                                partners_to_add.append(pid)
                                print(f"   🔍 {t['partenaire_nom']} → {pid}")
                            else:
                                print(f"   ❓ {t['partenaire_nom']} introuvable")
                            time.sleep(0.5)  # petit délai entre recherches
                        elif t['partenaire_id']:
                            partners_to_add.append(t['partenaire_id'])

                    save_profile(conn, profile)

                    # ── Ajout nouveaux partenaires en queue ─────────
                    added = 0
                    seen = set()
                    for pid in partners_to_add:
                        if pid and pid != id_fft and pid not in seen:
                            seen.add(pid)
                            exists = conn.execute(
                                "SELECT COUNT(*) FROM scrape_queue WHERE id_fft=?", (pid,)
                            ).fetchone()[0]
                            if not exists:
                                add_to_queue(conn, pid)
                                added += 1

                    if added:
                        print(f"   ➕ {added} nouveaux partenaires en queue")

                    mark_done(conn, id_fft)
                    scraped_this_run += 1

                except Exception as e:
                    print(f"   ❌ Erreur : {e}")
                    mark_error(conn, id_fft, e)

                delay = random.uniform(DELAY_MIN, DELAY_MAX)
                print(f"   ⏳ Pause {delay:.1f}s...")
                time.sleep(delay)

        finally:
            browser.close()

    total, done, pending, errors, joueurs, parts = stats(conn)
    print(f"\n{'='*50}")
    print(f"📊 Résumé :")
    print(f"   Joueurs scrapés  : {done}")
    print(f"   Encore en queue  : {pending}")
    print(f"   Erreurs          : {errors}")
    print(f"   Joueurs en base  : {joueurs}")
    print(f"   Participations   : {parts}")
    conn.close()

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, default=200,
                        help='Nombre max de joueurs (0=illimité)')
    args = parser.parse_args()
    run(limit=args.limit)
