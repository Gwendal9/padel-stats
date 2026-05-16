"""
Tenup HTTP Scraper — Version HTTPX (ultra-rapide, sans navigateur)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Bypasse Playwright/Chrome → requêtes HTTP directes → 10-30x plus rapide.

Le HTML de tenup.fft.fr est rendu côté serveur (Nuxt SSR) : la fiche
joueur est intégrée dans le HTML initial, sans besoin de JS.

Si curl_cffi est installé, il imite le fingerprint TLS de Chrome pour
mieux contourner DataDome. Sinon, httpx est utilisé.

Installation :
    pip install curl_cffi --break-system-packages   ← recommandé
    pip install httpx --break-system-packages        ← fallback

Usage :
    python scraper_http.py                     # 15 workers, illimité
    python scraper_http.py --workers 20        # 20 connexions parallèles
    python scraper_http.py --workers 8 --limit 5000
    python scraper_http.py --test --seed 7633273415
    python scraper_http.py --dump-html 7633273415  # debug : dump le HTML brut
"""

import asyncio
import sqlite3
import json
import re
import os
import time
import random
import argparse
from datetime import datetime, timedelta
from urllib.parse import unquote
from bs4 import BeautifulSoup

# ── Détection curl_cffi vs httpx ─────────────────────────────────
try:
    from curl_cffi.requests import AsyncSession as CurlSession
    USE_CURL_CFFI = True
    print("✅ curl_cffi disponible — fingerprint Chrome activé")
except ImportError:
    import httpx
    USE_CURL_CFFI = False
    print("⚠️  curl_cffi absent — utilisation de httpx (moins furtif)")
    print("   → pip install curl_cffi --break-system-packages")

# ── Config ────────────────────────────────────────────────────────
TENUP_BASE   = 'https://tenup.fft.fr'
COOKIES_FILE = os.path.join(os.path.dirname(__file__), 'cookies.json')
DB_FILE      = os.path.join(os.path.dirname(__file__), 'tenup.db')
DB_FILE_TEST = os.path.join(os.path.dirname(__file__), 'tenup_test.db')
STOP_FILE    = os.path.join(os.path.dirname(__file__), 'STOP')
DEBUG_HTML   = os.path.join(os.path.dirname(__file__), 'debug_page.html')
SEED_ID      = '7633273415'
MAX_RETRIES  = 3
BAN_THRESHOLD = 10   # Profils vides consécutifs avant STOP

# Délai par worker entre chaque requête
DELAY_MIN = 1.5
DELAY_MAX = 3.0

# Limiteur global : nb max de requêtes simultanées
CONCURRENCY_LIMIT = 15

HEADERS = {
    'User-Agent':                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept':                    'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
    'Accept-Language':           'fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7',
    'Accept-Encoding':           'gzip, deflate, br',
    'Connection':                'keep-alive',
    'Upgrade-Insecure-Requests': '1',
    'Sec-CH-UA':                 '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    'Sec-CH-UA-Mobile':          '?0',
    'Sec-CH-UA-Platform':        '"Windows"',
    'Sec-Fetch-Dest':            'document',
    'Sec-Fetch-Mode':            'navigate',
    'Sec-Fetch-Site':            'none',
    'Sec-Fetch-User':            '?1',
    'Cache-Control':             'max-age=0',
}

# ── Chargement des cookies ────────────────────────────────────────
def _load_raw_cookies():
    if not os.path.exists(COOKIES_FILE):
        raise FileNotFoundError("cookies.json introuvable")
    with open(COOKIES_FILE) as f:
        data = json.load(f)
    if isinstance(data, dict):
        return [{'name': k, 'value': v, 'domain': 'tenup.fft.fr', 'path': '/'}
                for k, v in data.items() if v and not v.startswith('COLLE_')]
    return data

def load_cookies_jar():
    """Retourne un dict {name: value} utilisable avec httpx / curl_cffi."""
    raw = _load_raw_cookies()
    jar = {}
    for c in raw:
        v = c.get('value', '')
        if v:
            jar[c['name']] = v
    print(f"✅ {len(jar)} cookies chargés")
    return jar

# ── Base de données ───────────────────────────────────────────────
def init_db(db_path=None):
    if db_path is None:
        db_path = DB_FILE
    conn = sqlite3.connect(db_path, isolation_level=None, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS joueurs (
            id_fft               TEXT PRIMARY KEY,
            nom                  TEXT,
            prenom               TEXT,
            ville                TEXT,
            club_nom             TEXT,
            echelon              TEXT,
            classement           INTEGER,
            meilleur_classement  INTEGER,
            sexe                 TEXT,
            naissance            TEXT,
            niveau               TEXT,
            scraped_at           TEXT
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
            id_fft          TEXT PRIMARY KEY,
            statut          TEXT DEFAULT 'pending',
            added_at        TEXT,
            processing_at   TEXT,
            scraped_at      TEXT,
            error           TEXT,
            worker_id       TEXT,
            retries         INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_queue_statut ON scrape_queue(statut);
        CREATE INDEX IF NOT EXISTS idx_part_joueur  ON participations(id_joueur);
        CREATE INDEX IF NOT EXISTS idx_part_partner ON participations(partenaire_id);
    ''')
    for migration in [
        "ALTER TABLE joueurs ADD COLUMN classement INTEGER",
        "ALTER TABLE joueurs ADD COLUMN meilleur_classement INTEGER",
        "ALTER TABLE joueurs ADD COLUMN niveau TEXT",
        "ALTER TABLE joueurs ADD COLUMN club_nom TEXT",
        "ALTER TABLE scrape_queue ADD COLUMN worker_id TEXT",
        "ALTER TABLE scrape_queue ADD COLUMN processing_at TEXT",
        "ALTER TABLE scrape_queue ADD COLUMN retries INTEGER DEFAULT 0",
    ]:
        try:
            conn.execute(migration)
            conn.commit()
        except:
            pass
    conn.commit()
    return conn

def add_to_queue(conn, id_fft, force=False):
    if force:
        conn.execute(
            "INSERT INTO scrape_queue (id_fft, statut, added_at, retries) VALUES (?, 'pending', ?, 0) "
            "ON CONFLICT(id_fft) DO UPDATE SET statut='pending', retries=0, worker_id=NULL",
            (id_fft, datetime.now().isoformat())
        )
    else:
        conn.execute(
            "INSERT OR IGNORE INTO scrape_queue (id_fft, statut, added_at) VALUES (?, 'pending', ?)",
            (id_fft, datetime.now().isoformat())
        )
    conn.commit()

def reset_stuck_processing(conn, timeout_minutes=20):
    cutoff = (datetime.now() - timedelta(minutes=timeout_minutes)).isoformat()
    r1 = conn.execute(
        "UPDATE scrape_queue SET statut='pending', worker_id=NULL "
        "WHERE statut='processing' AND processing_at IS NOT NULL AND processing_at < ?",
        (cutoff,)
    ).rowcount
    r2 = conn.execute(
        "UPDATE scrape_queue SET statut='pending', worker_id=NULL "
        "WHERE statut='processing' AND processing_at IS NULL AND added_at < ?",
        (cutoff,)
    ).rowcount
    conn.commit()
    total = r1 + r2
    if total:
        print(f"♻️  {total} joueurs bloqués remis en pending")

def get_next(conn, worker_id):
    for attempt in range(20):
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT id_fft FROM scrape_queue "
                "WHERE statut IN ('pending','update') AND (retries IS NULL OR retries < ?) "
                "ORDER BY added_at ASC LIMIT 1",
                (MAX_RETRIES,)
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE scrape_queue SET statut='processing', worker_id=?, "
                    "processing_at=?, retries=COALESCE(retries,0)+1 WHERE id_fft=?",
                    (worker_id, datetime.now().isoformat(), row[0])
                )
            conn.execute("COMMIT")
            return row[0] if row else None
        except sqlite3.OperationalError as e:
            try:
                conn.execute("ROLLBACK")
            except:
                pass
            if "locked" in str(e).lower():
                time.sleep(0.3 + random.random() * 0.3)
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

def save_profile(conn, profile):
    now  = datetime.now().isoformat()
    mois = datetime.now().strftime('%Y-%m')
    conn.execute('''
        INSERT INTO joueurs
            (id_fft, nom, prenom, ville, club_nom, echelon,
             classement, meilleur_classement, variation_classement,
             classement_date, sexe, naissance, scraped_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id_fft) DO UPDATE SET
            nom                  = excluded.nom,
            prenom               = excluded.prenom,
            ville                = excluded.ville,
            club_nom             = COALESCE(NULLIF(excluded.club_nom, ''), joueurs.club_nom),
            echelon              = excluded.echelon,
            classement           = COALESCE(excluded.classement, joueurs.classement),
            meilleur_classement  = COALESCE(excluded.meilleur_classement, joueurs.meilleur_classement),
            variation_classement = excluded.variation_classement,
            classement_date      = excluded.classement_date,
            sexe                 = excluded.sexe,
            naissance            = excluded.naissance,
            scraped_at           = excluded.scraped_at
    ''', (
        profile['id_fft'], profile['nom'], profile['prenom'],
        profile['ville'], profile.get('club_nom', ''),
        profile['echelon'], profile['classement'],
        profile.get('meilleur_classement'),
        profile.get('variation_classement'),
        mois,
        profile['sexe'], profile['naissance'],
        now
    ))
    # Snapshot mensuel
    if profile.get('classement') is not None:
        try:
            conn.execute('''
                INSERT INTO classements_historique
                    (id_fft, mois, classement, variation, meilleur_classement, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id_fft, mois) DO UPDATE SET
                    classement          = excluded.classement,
                    variation           = excluded.variation,
                    meilleur_classement = excluded.meilleur_classement,
                    scraped_at          = excluded.scraped_at
            ''', (
                profile['id_fft'], mois,
                profile['classement'],
                profile.get('variation_classement'),
                profile.get('meilleur_classement'),
                now
            ))
        except Exception:
            pass  # Table pas encore migrée
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
                partenaire_id = COALESCE(NULLIF(excluded.partenaire_id,''), participations.partenaire_id)
        ''', (
            profile['id_fft'], t['id_tournoi'], t['partenaire_id'],
            t['partenaire_nom'], t['date'], t['position'],
            t['points'], t['expiration'], t['type']
        ))
    conn.commit()

# ── Détection et bypass Queue-IT ─────────────────────────────────
def is_queueit_page(html):
    """Détecte si le HTML est une page de challenge Queue-IT."""
    return 'enqueuetoken' in html and 'decodeURIComponent' in html

async def bypass_queueit(session, html, semaphore):
    """
    Suit la redirection JavaScript de Queue-IT pour obtenir le cookie d'acceptation.
    Queue-IT émet un token valide ~4 minutes → on doit suivre immédiatement.
    Retourne le HTML final (page joueur) ou None.
    """
    # Extrait l'URL encodée du code JS : document.location.href = decodeURIComponent('...')
    match = re.search(r"decodeURIComponent\('([^']+)'\)", html)
    if not match:
        return None

    # Décode l'URL relative Queue-IT : /?c=tenup&e=tenupprod&enqueuetoken=...
    relative_url = unquote(match.group(1))
    if not relative_url.startswith('/'):
        relative_url = '/' + relative_url

    queueit_url = f"https://tenup.fft.fr{relative_url}"
    print(f"   🔄 Queue-IT détecté — suivi de la redirection...")

    headers = dict(HEADERS)
    headers['Referer'] = f'{TENUP_BASE}/accueil'

    async with semaphore:
        try:
            if USE_CURL_CFFI:
                resp = await session.get(queueit_url, headers=headers,
                                         timeout=30, allow_redirects=True)
            else:
                resp = await session.get(queueit_url, headers=headers, timeout=30.0)

            if resp.status_code == 200 and not is_queueit_page(resp.text):
                print(f"   ✅ Queue-IT contourné ({len(resp.text)} chars)")
                return resp.text
            else:
                print(f"   ⚠️  Queue-IT : réponse inattendue HTTP {resp.status_code} ({len(resp.text)} chars)")
                return None
        except Exception as e:
            print(f"   ⚠️  Queue-IT bypass erreur : {e}")
            return None

# ── Requête HTTP ──────────────────────────────────────────────────
async def fetch_html(session, id_fft, semaphore, dump_html=False):
    """
    Récupère le HTML de la page /classement/{id}/padel.
    Gère automatiquement les challenges Queue-IT.
    Retourne (html_str, status_code) ou (None, code) en cas d'échec.
    """
    url = f"{TENUP_BASE}/classement/{id_fft}/padel"
    headers = dict(HEADERS)
    headers['Referer'] = random.choice([
        f'{TENUP_BASE}/accueil',
        f'{TENUP_BASE}/recherche/joueurs/padel',
    ])

    async with semaphore:
        try:
            if USE_CURL_CFFI:
                resp = await session.get(url, headers=headers, timeout=30, allow_redirects=True)
            else:
                resp = await session.get(url, headers=headers, timeout=30.0)

            status = resp.status_code
            text   = resp.text if status == 200 else None

        except Exception as e:
            return None, str(e)

    if text is None:
        return None, status

    # ── Bypass Queue-IT si nécessaire ────────────────────────────
    if is_queueit_page(text):
        text = await bypass_queueit(session, text, semaphore)
        if text is None:
            return None, 'queueit_failed'
        status = 200

    if dump_html and text:
        with open(DEBUG_HTML, 'w', encoding='utf-8') as f:
            f.write(text)
        print(f"   📄 HTML dumpé dans {DEBUG_HTML} ({len(text)} chars)")

    return text, status

# ── Parser ────────────────────────────────────────────────────────
def parse_profile(html, id_fft):
    """
    Extrait le profil joueur depuis le HTML SSR de Nuxt.
    La fiche JSON est embarquée dans le HTML sous forme de variable JS.
    """
    profile = {
        'id_fft': id_fft, 'nom': '', 'prenom': '', 'ville': '',
        'echelon': '', 'classement': None, 'sexe': '', 'naissance': '',
        'club_nom': '', 'meilleur_classement': None,
        'variation_classement': None,   # delta mensuel FFT ("Places")
        'tournaments': []
    }

    # ── Extraction du JSON embarqué ──────────────────────────────
    fiche = {}

    # Cherche plusieurs marqueurs possibles dans le HTML Nuxt
    markers = ('"fft_fiche_joueur"', '"ficheJoueur"', '"profil"',
               '"vuejs_context"', '"dernierClassementPadel"')

    for marker in markers:
        idx = html.find(marker)
        if idx == -1:
            continue
        # Remonter au début du bloc JSON englobant
        # Cherche le { le plus proche AVANT le marqueur
        search_start = max(0, idx - 2000)
        chunk = html[search_start:idx]
        # Trouver le dernier '{' avant le marqueur → début de l'objet parent
        brace_pos = chunk.rfind('{')
        if brace_pos == -1:
            # Essayer après le marqueur
            start = html.find('{', idx)
        else:
            start = search_start + brace_pos

        if start == -1:
            continue

        depth, end = 0, start
        for i, c in enumerate(html[start:], start):
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break

        try:
            candidate = json.loads(html[start:end])
            if not isinstance(candidate, dict):
                continue
            # Vérifier que c'est bien une fiche joueur (a nom/prenom OU dernierClassementPadel)
            if candidate.get('nom') or candidate.get('prenom') or candidate.get('dernierClassementPadel'):
                fiche = candidate
                break
        except:
            # Peut-être que l'objet est imbriqué — essayer de chercher la clé directement
            continue

    # ── Fallback : chercher __NUXT__ ou window.__INITIAL_STATE__ ──
    if not fiche:
        for nuxt_marker in ('window.__NUXT__=', '__NUXT_DATA__', 'window.__INITIAL_STATE__='):
            idx = html.find(nuxt_marker)
            if idx == -1:
                continue
            start = html.find('{', idx)
            if start == -1:
                continue
            depth, end = 0, start
            for i, c in enumerate(html[start:], start):
                if c == '{':
                    depth += 1
                elif c == '}':
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
            try:
                nuxt_data = json.loads(html[start:end])
                # Chercher récursivement une sous-clé qui ressemble à une fiche joueur
                def find_fiche(d, depth=0):
                    if depth > 8:
                        return None
                    if isinstance(d, dict):
                        if d.get('nom') and d.get('prenom'):
                            return d
                        for v in d.values():
                            r = find_fiche(v, depth+1)
                            if r:
                                return r
                    elif isinstance(d, list):
                        for item in d:
                            r = find_fiche(item, depth+1)
                            if r:
                                return r
                    return None
                fiche = find_fiche(nuxt_data) or {}
                if fiche:
                    break
            except:
                continue

    if not fiche:
        return profile

    # ── Extraction des champs ────────────────────────────────────
    try:
        profile['nom']       = fiche.get('nom', '')
        profile['prenom']    = fiche.get('prenom', '')
        profile['ville']     = fiche.get('ville', fiche.get('commune', ''))
        profile['sexe']      = fiche.get('sexe', fiche.get('genre', ''))
        profile['naissance'] = fiche.get('birthYear', fiche.get('anneeNaissance',
                               fiche.get('dateNaissance', '')))

        # ── Échelon tennis ──────────────────────────────────────
        ct = fiche.get('classementTennis') or {}
        dc = ct.get('dernierClassement') or {}
        if dc.get('echelon'):
            profile['echelon'] = str(dc['echelon'])
        elif fiche.get('echelon'):
            profile['echelon'] = str(fiche['echelon'])

        # ── Club ────────────────────────────────────────────────
        club_obj = fiche.get('club')
        if isinstance(club_obj, dict):
            profile['club_nom'] = (club_obj.get('nom') or '').strip()
        elif isinstance(club_obj, str) and club_obj.strip():
            profile['club_nom'] = club_obj.strip()
        if not profile['club_nom']:
            for ck in ('nomClubRattachement', 'nomClub', 'libelleClub', 'clubNom',
                       'structureLibelle', 'nomStructure', 'libelleCentre'):
                cv = fiche.get(ck)
                if cv and isinstance(cv, str) and cv.strip():
                    profile['club_nom'] = cv.strip()
                    break

        # ── Classement padel ────────────────────────────────────
        for cl_key in ('dernierClassementPadel', 'classementPadel'):
            cl_val = fiche.get(cl_key)
            if cl_val and cl_val != 'NC':
                try:
                    profile['classement'] = int(cl_val)
                    break
                except (ValueError, TypeError):
                    pass
        if profile['classement'] is None:
            # Format XHR : rangsParPratique[].dernierRang
            for rang_entry in (fiche.get('rangsParPratique') or []):
                if (rang_entry.get('pratique') or {}).get('code', '').upper() == 'PADEL':
                    try:
                        profile['classement'] = int(rang_entry['dernierRang'])
                    except (KeyError, TypeError, ValueError):
                        pass
                    break
        if profile['classement'] is None:
            # Format alternatif : classement.PADEL.dernier.libelle
            cl_obj = fiche.get('classement') or {}
            if isinstance(cl_obj, dict):
                padel_cl = cl_obj.get('PADEL') or {}
                dernier  = padel_cl.get('dernier') or padel_cl.get('dernierClassement') or {}
                val = dernier.get('libelle') or dernier.get('rang')
                if val and val != 'NC':
                    try:
                        profile['classement'] = int(val)
                    except (ValueError, TypeError):
                        pass

        # ── Meilleur classement padel ───────────────────────────
        for mk in ('meilleurClassementPadel', 'meilleurClassement'):
            mv = fiche.get(mk)
            if mv and mv != 'NC':
                try:
                    profile['meilleur_classement'] = int(mv)
                    break
                except (ValueError, TypeError):
                    pass
        if profile['meilleur_classement'] is None:
            for rang_entry in (fiche.get('rangsParPratique') or []):
                if (rang_entry.get('pratique') or {}).get('code', '').upper() == 'PADEL':
                    try:
                        profile['meilleur_classement'] = int(rang_entry['meilleurRang'])
                    except (KeyError, TypeError, ValueError):
                        pass
                    break
        if profile['meilleur_classement'] is None:
            cl_obj = fiche.get('classement') or {}
            if isinstance(cl_obj, dict):
                padel_cl = cl_obj.get('PADEL') or {}
                meilleur = padel_cl.get('meilleur') or {}
                val = meilleur.get('libelle') or meilleur.get('rang')
                if val and val != 'NC':
                    try:
                        profile['meilleur_classement'] = int(val)
                    except (ValueError, TypeError):
                        pass

        # ── Variation mensuelle (Places sur TenUp) ─────────────────
        for rang_entry in (fiche.get('rangsParPratique') or []):
            if (rang_entry.get('pratique') or {}).get('code', '').upper() == 'PADEL':
                for evol_key in ('evolutionRang', 'evolution', 'nbEvolution',
                                 'rankEvolution', 'variation', 'rangEvolution'):
                    ev = rang_entry.get(evol_key)
                    if ev is not None:
                        try:
                            profile['variation_classement'] = int(ev)
                            break
                        except (TypeError, ValueError):
                            pass
                break
        if profile['variation_classement'] is None:
            for evol_key in ('evolutionClassementPadel', 'placesEvolution',
                             'evolutionRang', 'evolution'):
                ev = fiche.get(evol_key)
                if ev is not None and ev != 'NC':
                    try:
                        profile['variation_classement'] = int(ev)
                        break
                    except (TypeError, ValueError):
                        pass

    except Exception as e:
        print(f"   ⚠️  parse fiche error ({id_fft}): {e}")

    # ── Table des tournois (HTML brut) ───────────────────────────
    soup = BeautifulSoup(html, 'html.parser')
    for table in soup.select('table'):
        for row in table.select('tbody tr'):
            cells = row.select('td')
            if len(cells) < 5:
                continue
            texts = [c.get_text(strip=True) for c in cells]
            if not re.match(r'\d{2}/\d{2}/\d{4}', texts[0]):
                continue

            id_tournoi = ''
            lien       = ''
            link_el    = cells[1].select_one('a[href]') if len(cells) > 1 else None
            if link_el:
                href = link_el.get('href', '')
                lien = f"{TENUP_BASE}{href}" if href.startswith('/') else href
                m = re.search(r'/tournoi/(\d+)', lien)
                if m:
                    id_tournoi = m.group(1)

            partenaire_id = ''
            if len(cells) > 4:
                p_link = cells[4].select_one('a[href]')
                if p_link:
                    pm = re.search(r'/(?:classement|fichejoueur)/(\d+)', p_link.get('href', ''))
                    if pm:
                        partenaire_id = pm.group(1)

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

# ── Worker ────────────────────────────────────────────────────────
async def worker(worker_id, session, semaphore, conn, db_lock, counter, args):
    wid               = f"W{worker_id}"
    pid_str           = f"{os.getpid()}-{worker_id}"
    empty_run         = 0
    consecutive_empty = 0
    dump_html_once    = args.dump_html and worker_id == 0

    while True:
        if os.path.exists(STOP_FILE):
            print(f"   [{wid}] ⛔ STOP détecté — arrêt.")
            break

        if args.limit > 0 and counter['scraped'] >= args.limit:
            break

        # ── Claim joueur ─────────────────────────────────────────
        async with db_lock:
            id_fft = await asyncio.to_thread(get_next, conn, pid_str)

        if not id_fft:
            empty_run += 1
            if empty_run >= 3:
                print(f"   [{wid}] ✅ Queue vide.")
                break
            await asyncio.sleep(5)
            continue

        empty_run = 0

        async with db_lock:
            total, done, pending, processing, errors, joueurs, parts = await asyncio.to_thread(stats, conn)

        print(f"[{done+1}/{total}] [{wid}] 🌐 {id_fft} — pending:{pending} done:{done}")

        try:
            # ── Fetch HTTP ───────────────────────────────────────
            html, status = await fetch_html(session, id_fft, semaphore,
                                            dump_html=dump_html_once)
            dump_html_once = False  # une seule fois

            if html is None:
                consecutive_empty += 1
                print(f"   [{wid}] ⚠️  HTTP {status} pour {id_fft} — vide #{consecutive_empty}")

                if str(status) in ('403', '429') or consecutive_empty >= BAN_THRESHOLD:
                    print(f"\n🚨 [{wid}] Trop d'erreurs HTTP ({status}) — possible BAN DataDome !")
                    with open(STOP_FILE, 'w') as f:
                        f.write(f"Erreur HTTP {status} — {pid_str} — {datetime.now().isoformat()}\n")
                    async with db_lock:
                        conn.execute(
                            "UPDATE scrape_queue SET statut='pending', worker_id=NULL WHERE id_fft=?",
                            (id_fft,)
                        )
                        conn.commit()
                    break

                # Remettre en pending pour réessayer
                async with db_lock:
                    conn.execute(
                        "UPDATE scrape_queue SET statut='pending', worker_id=NULL WHERE id_fft=?",
                        (id_fft,)
                    )
                    conn.commit()
                await asyncio.sleep(random.uniform(5, 15))
                continue

            # ── Parse ────────────────────────────────────────────
            profile = parse_profile(html, id_fft)

            if not profile['nom']:
                consecutive_empty += 1
                retries_done = conn.execute(
                    "SELECT retries FROM scrape_queue WHERE id_fft=?", (id_fft,)
                ).fetchone()
                retries_done = retries_done[0] if retries_done else 1
                print(f"   [{wid}] ⚠️  Profil vide — tentative {retries_done}/{MAX_RETRIES} (vides consécutifs: {consecutive_empty})")

                if retries_done >= MAX_RETRIES:
                    async with db_lock:
                        await asyncio.to_thread(mark_error, conn, id_fft,
                                                f"profil vide après {retries_done} tentatives")
                    counter['scraped'] += 1
                    continue

                if consecutive_empty >= BAN_THRESHOLD:
                    print(f"\n🚨 [{wid}] {BAN_THRESHOLD} profils vides consécutifs — BAN possible !")
                    with open(STOP_FILE, 'w') as f:
                        f.write(f"Ban détecté par {pid_str} à {datetime.now().isoformat()}\n")
                    async with db_lock:
                        conn.execute(
                            "UPDATE scrape_queue SET statut='pending', worker_id=NULL WHERE id_fft=?",
                            (id_fft,)
                        )
                        conn.commit()
                    break

                async with db_lock:
                    conn.execute(
                        "UPDATE scrape_queue SET statut='pending', worker_id=NULL WHERE id_fft=?",
                        (id_fft,)
                    )
                    conn.commit()
                continue

            consecutive_empty = 0

            club_str  = f" | club:{profile['club_nom']}" if profile['club_nom'] else ""
            curr      = profile.get('classement')
            best      = profile.get('meilleur_classement')
            rank_str  = f" | #{curr}" if curr else ""
            rank_str += f" (best #{best})" if best and best != curr else ""
            print(f"   [{wid}] 👤 {profile['prenom']} {profile['nom']}"
                  f" — {len(profile['tournaments'])} tournois{club_str}{rank_str}")

            # ── Sauvegarde ───────────────────────────────────────
            async with db_lock:
                await asyncio.to_thread(save_profile, conn, profile)

                added = 0
                seen  = set()
                for t in profile['tournaments']:
                    pid = t.get('partenaire_id')
                    if pid and pid != id_fft and pid not in seen:
                        seen.add(pid)
                        exists = conn.execute(
                            "SELECT COUNT(*) FROM scrape_queue WHERE id_fft=?", (pid,)
                        ).fetchone()[0]
                        if not exists:
                            add_to_queue(conn, pid)
                            added += 1

                await asyncio.to_thread(mark_done, conn, id_fft)

            if added:
                print(f"   [{wid}] ➕ {added} nouveaux partenaires")

            counter['scraped'] += 1

        except Exception as e:
            print(f"   [{wid}] ❌ Erreur {id_fft}: {e}")
            async with db_lock:
                await asyncio.to_thread(mark_error, conn, id_fft, e)

        # ── Délai anti-détection ─────────────────────────────────
        await asyncio.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

    # Libérer les joueurs claim
    async with db_lock:
        conn.execute(
            "UPDATE scrape_queue SET statut='pending', worker_id=NULL "
            "WHERE statut='processing' AND worker_id=?",
            (pid_str,)
        )
        conn.commit()

# ── Point d'entrée ────────────────────────────────────────────────
async def run_async(args):
    n       = args.workers
    db_path = DB_FILE_TEST if args.test else DB_FILE
    seed    = args.seed or SEED_ID

    print(f"\n🎾 Tenup HTTP Scraper — {n} workers (sans navigateur)")
    print(f"📁 Base : {db_path}" + (" ⚠️  MODE TEST" if args.test else ""))
    print(f"🔗 Backend : {'curl_cffi (Chrome TLS)' if USE_CURL_CFFI else 'httpx'}")
    if args.dump_html:
        print(f"🔍 Debug : premier HTML dumpé dans {DEBUG_HTML}")

    cookies = load_cookies_jar()
    conn    = init_db(db_path)

    add_to_queue(conn, seed, force=(args.test or args.seed is not None))
    reset_stuck_processing(conn)

    if os.path.exists(STOP_FILE):
        print(f"⛔ Fichier STOP existant — supprime-le d'abord : del STOP  (Windows) ou rm STOP")
        return

    counter   = {'scraped': 0}
    db_lock   = asyncio.Lock()
    semaphore = asyncio.Semaphore(n)  # Limite le nb de requêtes simultanées

    if USE_CURL_CFFI:
        async with CurlSession(impersonate="chrome124", cookies=cookies,
                               verify=True, max_redirects=5) as session:
            tasks = [
                asyncio.create_task(
                    worker(i, session, semaphore, conn, db_lock, counter, args)
                )
                for i in range(n)
            ]
            await asyncio.gather(*tasks)
    else:
        cookie_jar = httpx.Cookies(cookies)
        async with httpx.AsyncClient(
            cookies=cookie_jar,
            follow_redirects=True,
            http2=True,
            timeout=30.0,
            limits=httpx.Limits(max_connections=n+5, max_keepalive_connections=n)
        ) as session:
            tasks = [
                asyncio.create_task(
                    worker(i, session, semaphore, conn, db_lock, counter, args)
                )
                for i in range(n)
            ]
            await asyncio.gather(*tasks)

    total, done, pending, processing, errors, joueurs, parts = stats(conn)
    print(f"\n{'='*55}")
    print(f"📊 Résumé final :")
    print(f"   Scrapés ce run    : {counter['scraped']}")
    print(f"   Total done en DB  : {done}")
    print(f"   Encore en queue   : {pending}")
    print(f"   Erreurs           : {errors}")
    print(f"   Joueurs en base   : {joueurs}")
    print(f"   Participations    : {parts}")
    conn.close()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Tenup HTTP Scraper (ultra-rapide, sans navigateur)')
    parser.add_argument('--workers',   type=int,  default=15,
                        help='Nb de requêtes parallèles (défaut: 15)')
    parser.add_argument('--limit',     type=int,  default=0,
                        help='Joueurs max (0=illimité)')
    parser.add_argument('--test',      action='store_true',
                        help='Utilise tenup_test.db (ne touche pas tenup.db)')
    parser.add_argument('--seed',      type=str,  default=None,
                        help='ID FFT de départ (ex: --seed 7633273415)')
    parser.add_argument('--dump-html', action='store_true',
                        help='Dump le HTML du premier joueur dans debug_page.html')
    args = parser.parse_args()
    asyncio.run(run_async(args))
