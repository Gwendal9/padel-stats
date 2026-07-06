"""
Tenup Cascade Scraper
─────────────────────
Démarre depuis un joueur seed, scrape son profil,
découvre ses partenaires via l'API REST, les ajoute à la queue, et répète.

Usage :
    python scraper.py                      -> limite par défaut (200 joueurs)
    python scraper.py --limit 1000         -> limite custom
    python scraper.py --limit 0            -> illimité (attention !)
    python scraper.py --headless           -> navigateur invisible (plus rapide, risque détection)

    Multi-process (lancer dans 2-3 terminaux simultanément) :
    python scraper.py --limit 1000         -> terminal 1
    python scraper.py --limit 1000         -> terminal 2
    python scraper.py --limit 1000         -> terminal 3

Optimisations v2 :
    - Contexte Playwright réutilisé entre joueurs (~30% plus rapide)
    - Mode headless optionnel via --headless
    - Multi-process safe : queue atomique via worker_id + SQLite WAL
"""

import sqlite3
import json
import re
import os
import time
import random
import argparse
import requests
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# Config
TENUP_BASE      = 'https://tenup.fft.fr'
COOKIES_FILE    = os.path.join(os.path.dirname(__file__), 'cookies.json')
DB_FILE         = os.path.join(os.path.dirname(__file__), 'tenup.db')
STOP_FILE       = os.path.join(os.path.dirname(__file__), 'STOP')
SEED_ID         = '7633273415'
DELAY_MIN       = 2.5   # Réduit grâce au blocage ressources (anciennement 5.0)
DELAY_MAX       = 5.0   # Réduit (anciennement 10.0)
API_SEARCH_URL  = f'{TENUP_BASE}/back/v1/personnes/joueurs-padel'

# Nb de profils vides consécutifs avant d'activer l'arrêt d'urgence
BAN_THRESHOLD   = 5

# ── Cookies ───────────────────────────────────────────────────────
COOKIE_DOMAINS = {
    'SHARED_SESSION_JAVA':                      'tenup.fft.fr',
    'SSESS7ba44afc36c80c3faa2b8fa87e7742c5':    '.fft.fr',
    'datadome':                                  '.fft.fr',
    'QueueITAccepted-SDFrts345E-V3_tenupprod':  'tenup.fft.fr',
}

SAMESIDE_MAP = {
    'no_restriction': 'None',
    'unspecified':    'None',
    'lax':            'Lax',
    'strict':         'Strict',
    'none':           'None',
}

def _load_raw_cookies():
    """Charge cookies.json — accepte Cookie-Editor (liste) ou ancien format (dict)."""
    if not os.path.exists(COOKIES_FILE):
        raise FileNotFoundError("cookies.json introuvable")
    with open(COOKIES_FILE) as f:
        data = json.load(f)
    if isinstance(data, dict):
        return [{'name': k, 'value': v, 'domain': COOKIE_DOMAINS.get(k, 'tenup.fft.fr'), 'path': '/'}
                for k, v in data.items() if v and not v.startswith('COLLE_')]
    return data

def load_cookies():
    raw = _load_raw_cookies()
    cookies = []
    for c in raw:
        v = c.get('value', '')
        if not v:
            continue
        cookies.append({
            'name':   c['name'],
            'value':  v,
            'domain': c.get('domain', 'tenup.fft.fr'),
            'path':   c.get('path', '/'),
        })
    print(f"✅ {len(cookies)} cookies chargés")
    return cookies

def init_session():
    """Session requests pour l'API REST (recherche joueur)"""
    raw = _load_raw_cookies()
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Content-Type': 'application/json',
        'Referer': f'{TENUP_BASE}/recherche/joueurs/padel',
        'Origin': TENUP_BASE,
    })
    for c in raw:
        v = c.get('value', '')
        if v and not v.startswith('COLLE_'):
            domain = c.get('domain', 'tenup.fft.fr').lstrip('.')
            session.cookies.set(c['name'], v, domain=domain)
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
    # isolation_level=None = autocommit — on gère les transactions manuellement
    # C'est nécessaire pour pouvoir utiliser BEGIN IMMEDIATE dans get_next()
    conn = sqlite3.connect(DB_FILE, isolation_level=None)
    # Multi-process : WAL permet plusieurs lecteurs + 1 écrivain simultané
    conn.execute("PRAGMA journal_mode=WAL")
    # Attend jusqu'à 10s si la DB est verrouillée (au lieu de planter)
    conn.execute("PRAGMA busy_timeout=10000")
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
    # Migrations : ajouter colonnes manquantes si absentes
    for migration in [
        "ALTER TABLE joueurs ADD COLUMN classement INTEGER",
        "ALTER TABLE joueurs ADD COLUMN meilleur_classement INTEGER",
        "ALTER TABLE joueurs ADD COLUMN niveau TEXT",
        "ALTER TABLE joueurs ADD COLUMN club_nom TEXT",
        # worker_id : identifie quel process a claim ce joueur (multi-process)
        "ALTER TABLE scrape_queue ADD COLUMN worker_id TEXT",
    ]:
        try:
            conn.execute(migration)
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

def reset_stuck_processing(conn, timeout_minutes=30):
    """
    Remet en 'pending' les joueurs bloqués en 'processing' depuis trop longtemps.
    Utile si un process a planté sans nettoyer derrière lui.
    """
    cutoff = (datetime.now() - timedelta(minutes=timeout_minutes)).isoformat()
    reset = conn.execute("""
        UPDATE scrape_queue SET statut='pending', worker_id=NULL
        WHERE statut='processing' AND added_at < ?
    """, (cutoff,)).rowcount
    conn.commit()
    if reset:
        print(f"♻️  {reset} joueurs bloqués remis en pending (process mort ?)")

def get_next(conn):
    """
    Claim atomiquement le prochain joueur pending → processing.
    BEGIN IMMEDIATE verrouille la DB en écriture dès le début de la transaction :
    deux process ne peuvent pas lire/écrire simultanément dans cette section,
    donc impossible de claim le même joueur.
    """
    worker_id = str(os.getpid())
    for attempt in range(20):
        try:
            # Verrouille la DB en écriture immédiatement (les autres process attendent)
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT id_fft FROM scrape_queue WHERE statut IN ('pending','update') ORDER BY added_at ASC LIMIT 1"
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE scrape_queue SET statut='processing', worker_id=? WHERE id_fft=?",
                    (worker_id, row[0])
                )
            conn.execute("COMMIT")
            return row[0] if row else None
        except sqlite3.OperationalError as e:
            try:
                conn.execute("ROLLBACK")
            except:
                pass
            if "locked" in str(e).lower():
                time.sleep(0.5 + random.random() * 0.5)
                continue
            raise
    return None

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
    total      = conn.execute("SELECT COUNT(*) FROM scrape_queue").fetchone()[0]
    done       = conn.execute("SELECT COUNT(*) FROM scrape_queue WHERE statut='done'").fetchone()[0]
    pending    = conn.execute("SELECT COUNT(*) FROM scrape_queue WHERE statut='pending'").fetchone()[0]
    processing = conn.execute("SELECT COUNT(*) FROM scrape_queue WHERE statut='processing'").fetchone()[0]
    errors     = conn.execute("SELECT COUNT(*) FROM scrape_queue WHERE statut='error'").fetchone()[0]
    joueurs    = conn.execute("SELECT COUNT(*) FROM joueurs").fetchone()[0]
    parts      = conn.execute("SELECT COUNT(*) FROM participations").fetchone()[0]
    return total, done, pending, processing, errors, joueurs, parts

# ── Playwright ────────────────────────────────────────────────────
def fetch_profile(context, id_fft):
    """
    Ouvre une page dans le contexte partagé.
    Stratégies d'optimisation :
    - Bloque images/fonts/CSS (inutiles pour le parsing)
    - Intercepte les réponses JSON API pour récupérer fiche + tournois directement
    - Évite wait_for_load_state('networkidle') qui peut durer 5-15s
    """
    url = f"{TENUP_BASE}/classement/{id_fft}/padel"
    page = context.new_page()

    # Données interceptées via les XHR de la page
    intercepted = {'fiche': None, 'classements': []}

    # ── Bloquer les ressources inutiles (images, fonts, CSS, vidéos) ──
    BLOCK_TYPES = {'image', 'media', 'font', 'stylesheet'}
    BLOCK_DOMAINS = ('google-analytics', 'googletagmanager', 'facebook', 'hotjar',
                     'doubleclick', 'analytics', 'datadome')

    def _route_handler(route):
        req = route.request
        if req.resource_type in BLOCK_TYPES:
            return route.abort()
        if any(d in req.url for d in BLOCK_DOMAINS):
            return route.abort()
        route.continue_()

    page.route('**/*', _route_handler)

    # ── Intercepter les réponses JSON de l'API FFT ──────────────────
    def _on_response(response):
        try:
            url_r = response.url
            ct = response.headers.get('content-type', '')
            if 'json' not in ct:
                return
            # Fiche joueur
            if '/fiche' in url_r or 'joueur' in url_r.lower():
                try:
                    intercepted['fiche'] = response.json()
                except:
                    pass
            # Classements / tournois
            if 'classement' in url_r.lower() or 'participation' in url_r.lower() or 'tournoi' in url_r.lower():
                try:
                    data = response.json()
                    if isinstance(data, list):
                        intercepted['classements'].extend(data)
                    elif isinstance(data, dict):
                        for key in ('data', 'items', 'results', 'classements', 'participations'):
                            if isinstance(data.get(key), list):
                                intercepted['classements'].extend(data[key])
                                break
                except:
                    pass
        except:
            pass

    page.on('response', _on_response)

    try:
        page.goto(url, wait_until='domcontentloaded', timeout=60000)

        if 'queue' in page.url.lower():
            print(f"   ⏳ Queue-it détecté... attente 30s")
            time.sleep(30)
            page.wait_for_url(f'**/classement/{id_fft}/**', timeout=120000)

        # Attendre soit la table, soit le JSON fiche — ce qui vient en premier
        # Timeout court : si la page est rapide, on ne perd pas de temps
        found_table = False
        try:
            page.wait_for_selector('table, [class*="fiche"], [class*="classement"]', timeout=10000)
            found_table = True
        except:
            pass

        # Si on n'a pas encore la table, petite attente réseau (max 5s)
        if not found_table:
            try:
                page.wait_for_load_state('networkidle', timeout=5000)
            except:
                pass

        html = page.content()

    finally:
        page.off('response', _on_response)
        page.close()

    return html, intercepted

# ── Parser ────────────────────────────────────────────────────────
def parse_profile(html, id_fft, intercepted=None):
    """
    Parse le profil depuis le HTML et/ou les données interceptées (XHR).
    `intercepted` est le dict retourné par fetch_profile : {'fiche': ..., 'classements': [...]}
    Les données interceptées ont priorité sur le parsing HTML (plus fiables, plus rapides).
    """
    profile = {
        'id_fft': id_fft,
        'nom': '', 'prenom': '', 'ville': '',
        'echelon': '', 'classement': None, 'meilleur_classement': None, 'sexe': '', 'naissance': '',
        'club_nom': '',
        'tournaments': []
    }
    if intercepted is None:
        intercepted = {}

    # ── Priorité 1 : fiche interceptée via XHR ──────────────────────
    fiche = intercepted.get('fiche') or {}

    # ── Priorité 2 : fiche embarquée dans le HTML ───────────────────
    if not fiche:
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
            except Exception as e:
                print(f"   ⚠️  fft_fiche_joueur parse error: {e}")

    if fiche:
        try:

            # ── DEBUG (première fois seulement) : afficher toutes les clés disponibles ──
            # Utile pour découvrir le nom exact du champ "nom de club"
            if not getattr(parse_profile, '_fiche_keys_logged', False):
                parse_profile._fiche_keys_logged = True
                print(f"   🔍 fft_fiche_joueur keys: {list(fiche.keys())}")
                # Afficher les valeurs non-null pour comprendre la structure
                for k, v in fiche.items():
                    if v and not isinstance(v, (dict, list)):
                        print(f"      {k!r}: {str(v)[:80]!r}")

            profile['nom']       = fiche.get('nom', '')
            profile['prenom']    = fiche.get('prenom', '')
            profile['ville']     = fiche.get('ville', '')
            profile['sexe']      = fiche.get('sexe', '')
            profile['naissance'] = fiche.get('birthYear', '')

            # ── Échelon : classementTennis.dernierClassement.echelon ────
            _ct = fiche.get('classementTennis') or {}
            _dc = _ct.get('dernierClassement') or {}
            if _dc.get('echelon'):
                profile['echelon'] = str(_dc['echelon'])
            elif fiche.get('echelon'):
                profile['echelon'] = str(fiche['echelon'])

            # ── Club : fiche['club']['nom'] ──────────────────────────────
            _club_obj = fiche.get('club')
            if isinstance(_club_obj, dict):
                profile['club_nom'] = (_club_obj.get('nom') or '').strip()
            elif isinstance(_club_obj, str) and _club_obj.strip():
                profile['club_nom'] = _club_obj.strip()
            if not profile['club_nom']:
                for _ck in ('nomClubRattachement', 'nomClub', 'libelleClub', 'clubNom',
                            'structureLibelle', 'nomStructure'):
                    _cv = fiche.get(_ck)
                    if _cv and isinstance(_cv, str) and _cv.strip():
                        profile['club_nom'] = _cv.strip()
                        break

            # ── Classement padel ─────────────────────────────────────────
            # Format HTML : fiche['dernierClassementPadel'] = '14335'
            for _cl_key in ('dernierClassementPadel', 'classementPadel'):
                _cl_val = fiche.get(_cl_key)
                if _cl_val and _cl_val != 'NC':
                    try:
                        profile['classement'] = int(_cl_val)
                        break
                    except (ValueError, TypeError):
                        pass
            if profile['classement'] is None:
                for _r in (fiche.get('rangsParPratique') or []):
                    if (_r.get('pratique') or {}).get('code', '').upper() == 'PADEL':
                        try:
                            profile['classement'] = int(_r['dernierRang'])
                        except (KeyError, TypeError, ValueError):
                            pass
                        break
            if profile['classement'] is None:
                for _cl_key in ('classement', 'rang', 'ranking', 'rangNational'):
                    _cl_val = fiche.get(_cl_key)
                    if _cl_val is not None and _cl_val != 'NC':
                        try:
                            profile['classement'] = int(_cl_val)
                            break
                        except (ValueError, TypeError):
                            pass

            # ── Meilleur classement padel ──
            for _mk in ('meilleurClassementPadel', 'meilleurClassement'):
                _mv = fiche.get(_mk)
                if _mv and _mv != 'NC':
                    try:
                        profile['meilleur_classement'] = int(_mv)
                        break
                    except (ValueError, TypeError):
                        pass
            if profile['meilleur_classement'] is None:
                for _r in (fiche.get('rangsParPratique') or []):
                    if (_r.get('pratique') or {}).get('code', '').upper() == 'PADEL':
                        try:
                            profile['meilleur_classement'] = int(_r['meilleurRang'])
                        except (KeyError, TypeError, ValueError):
                            pass
                        break

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
        INSERT INTO joueurs
            (id_fft, nom, prenom, ville, club_nom, echelon,
             classement, meilleur_classement, sexe, naissance, scraped_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id_fft) DO UPDATE SET
            nom                 = excluded.nom,
            prenom              = excluded.prenom,
            ville               = excluded.ville,
            club_nom            = COALESCE(NULLIF(excluded.club_nom,''), joueurs.club_nom),
            echelon             = excluded.echelon,
            classement          = COALESCE(excluded.classement, joueurs.classement),
            meilleur_classement = COALESCE(excluded.meilleur_classement, joueurs.meilleur_classement),
            sexe                = excluded.sexe,
            naissance           = excluded.naissance,
            scraped_at          = excluded.scraped_at
    ''', (
        profile['id_fft'], profile['nom'], profile['prenom'],
        profile['ville'], profile.get('club_nom', ''), profile['echelon'],
        profile['classement'], profile.get('meilleur_classement'),
        profile['sexe'], profile['naissance'],
        datetime.now().isoformat()
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
def run(limit=200, headless=False):
    print(f"🎾 Tenup Cascade Scraper — limite : {limit if limit > 0 else 'illimitée'}")
    print(f"📁 Base : {DB_FILE}")
    print(f"🖥️  Mode : {'headless (invisible)' if headless else 'visible'}")

    cookies = load_cookies()
    session = init_session()
    conn    = init_db()
    add_to_queue(conn, SEED_ID)

    # Remet en pending les joueurs bloqués en 'processing' depuis >30min
    # (cas d'un process qui a planté sans nettoyer)
    reset_stuck_processing(conn)

    # Vérifie qu'on ne démarre pas avec un fichier STOP existant
    if os.path.exists(STOP_FILE):
        print(f"⛔ Fichier STOP détecté — supprime-le avant de relancer : rm STOP")
        return

    scraped_this_run  = 0
    consecutive_empty = 0  # Compteur de profils vides consécutifs (détection ban)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=['--disable-blink-features=AutomationControlled']
        )
        # Contexte créé une seule fois et réutilisé pour tous les joueurs
        context = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            locale='fr-FR',
        )
        context.add_cookies(cookies)
        try:
            while True:
                # ── Vérification fichier STOP (arrêt manuel ou par un autre process) ──
                if os.path.exists(STOP_FILE):
                    print(f"\n⛔ [PID {os.getpid()}] Fichier STOP détecté — arrêt propre.")
                    # Remet notre joueur en pending pour qu'un prochain run le reprenne
                    conn.execute(
                        "UPDATE scrape_queue SET statut='pending', worker_id=NULL WHERE statut='processing' AND worker_id=?",
                        (str(os.getpid()),)
                    )
                    conn.commit()
                    break

                if limit > 0 and scraped_this_run >= limit:
                    print(f"\n✅ Limite de {limit} joueurs atteinte.")
                    break

                id_fft = get_next(conn)
                if not id_fft:
                    print("\n✅ Queue vide — tous les joueurs ont été scrapés !")
                    break

                total, done, pending, processing, errors, joueurs, parts = stats(conn)
                print(f"\n[{done+1}/{total}] 🎭 {id_fft} — "
                      f"pending:{pending} processing:{processing} done:{done} erreurs:{errors} "
                      f"| joueurs:{joueurs} participations:{parts}")

                try:
                    html, intercepted = fetch_profile(context, id_fft)
                    profile = parse_profile(html, id_fft, intercepted)

                    if profile['nom']:
                        print(f"   👤 {profile['prenom']} {profile['nom']} "
                              f"— {len(profile['tournaments'])} tournois")
                        consecutive_empty = 0  # Reset : profil OK
                    else:
                        consecutive_empty += 1
                        print(f"   ⚠️  Profil vide [{consecutive_empty}/{BAN_THRESHOLD}] "
                              f"(datadome ? session expirée ?)")

                        # ── Arrêt d'urgence si trop de profils vides consécutifs ──
                        if consecutive_empty >= BAN_THRESHOLD:
                            print(f"\n🚨 [PID {os.getpid()}] {BAN_THRESHOLD} profils vides consécutifs "
                                  f"— possible ban datadome !")
                            print(f"   Création du fichier STOP pour arrêter tous les process...")
                            with open(STOP_FILE, 'w') as f:
                                f.write(f"Ban détecté par PID {os.getpid()} à {datetime.now().isoformat()}\n")
                            # Remet le joueur en pending
                            conn.execute(
                                "UPDATE scrape_queue SET statut='pending', worker_id=NULL WHERE id_fft=?",
                                (id_fft,)
                            )
                            conn.commit()
                            break

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
            context.close()
            browser.close()

    total, done, pending, processing, errors, joueurs, parts = stats(conn)
    print(f"\n{'='*50}")
    print(f"📊 Résumé :")
    print(f"   Joueurs scrapés  : {done}")
    print(f"   En cours (autres process) : {processing}")
    print(f"   Encore en queue  : {pending}")
    print(f"   Erreurs          : {errors}")
    print(f"   Joueurs en base  : {joueurs}")
    print(f"   Participations   : {parts}")
    conn.close()

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, default=200,
                        help='Nombre max de joueurs (0=illimité)')
    parser.add_argument('--headless', action='store_true',
                        help='Navigateur invisible (plus rapide, risque détection accrue)')
    args = parser.parse_args()
    run(limit=args.limit, headless=args.headless)
