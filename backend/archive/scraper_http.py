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
import sys
import time
import random
import argparse
import subprocess
from datetime import datetime, timedelta
from urllib.parse import unquote, urlparse, urljoin
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
SHARED_SESSION = False  # False = session fraîche par joueur (recommandé)
WAIT_FILE = os.path.join(os.path.dirname(__file__), 'WAIT_COOKIES')  # legacy

# Délai par worker entre chaque requête
DELAY_MIN = 3.0
DELAY_MAX = 8.0

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


# ── Auto-refresh cookies via Playwright ──────────────────────────
_refresh_lock     = None   # asyncio.Lock() initialisé dans run_async
_last_refresh_ts  = 0.0    # timestamp du dernier refresh réussi
REFRESH_SCRIPT    = os.path.join(os.path.dirname(__file__), 'auto_refresh_cookies.py')

async def auto_refresh_cookies_async() -> bool:
    """
    Lance auto_refresh_cookies.py (Playwright --visible) pour rafraîchir les
    cookies QueueITAccepted automatiquement.

    DESIGN : le verrou _refresh_lock est tenu pendant TOUTE la durée du refresh.
    Les workers qui arrivent pendant ce temps ATTENDENT la fin (pas de retour anticipé).
    Une fois le refresh terminé, tous les workers rechargent les cookies frais.
    Un délai minimum de 30s entre deux refreshes évite les boucles infinies.
    """
    global _last_refresh_ts

    async with _refresh_lock:          # ← tenu jusqu'à la fin du refresh
        # Si un refresh vient juste d'avoir lieu (< 30s), considérer OK
        if time.time() - _last_refresh_ts < 30:
            return True

        print(f"\n{'='*60}")
        print("🤖 Auto-refresh cookies via Playwright (fenêtre visible)...")
        print(f"   Script : {REFRESH_SCRIPT}")
        print(f"{'='*60}")

        if not os.path.exists(REFRESH_SCRIPT):
            print("   ❌ auto_refresh_cookies.py introuvable")
            return False

        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, REFRESH_SCRIPT, '--visible',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=150)
            output = stdout.decode('utf-8', errors='replace') if stdout else ''
            if output.strip():
                print(output.strip())

            if proc.returncode == 0:
                print("   ✅ Auto-refresh réussi !")
                _last_refresh_ts = time.time()
                return True
            else:
                print(f"   ❌ Playwright échoué (code {proc.returncode})")
                print("   → Installe : pip install playwright && playwright install chromium")
                return False

        except asyncio.TimeoutError:
            print("   ❌ Timeout auto-refresh (>150s)")
            return False
        except Exception as e:
            print(f"   ❌ Erreur : {e}")
            return False

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

# Cookies à exclure du chargement général.
# IMPORTANT : SSESS* (session Drupal HTTPS) et SHARED_SESSION* (session Java)
# sont DÉSORMAIS INCLUS — les pages /classement/{id}/padel requièrent une
# session authentifiée (non-logged-in → 403 Accès refusé).
# Le risque de boucle redirect (session expirée → 302→/) est géré par
# max_redirects=5 dans fetch_html : au pire, empty profile → retry → refresh.
_EXCLUDED_COOKIE_PREFIXES = ()          # plus d'exclusion par préfixe
_EXCLUDED_COOKIE_NAMES    = frozenset({
    'userStore', 'pa_user', 'pa_vid', 'pa_privacy',
})

def load_cookies_jar():
    """Retourne un dict {name: value} avec tous les cookies utiles.

    Inclut les cookies de session Drupal/Java (SSESS*, SHARED_SESSION_JAVA)
    car les pages /classement/{id}/padel requièrent désormais une session
    authentifiée (Drupal retourne 403 pour les visiteurs anonymes).
    Cookies essentiels :
      - SSESS*                                    (session Drupal HTTPS — AUTH)
      - SHARED_SESSION_JAVA                       (session Java — AUTH)
      - QueueITAccepted-SDFrts345E-V3_tenupprod  (bypass Queue-IT)
      - datadome                                  (bypass DataDome — exclu de curl_cffi)
      - i18n_redirected                           (évite redirect langue)
      - TC_PRIVACY / TC_PRIVACY_CENTER            (consentement RGPD)
    """
    raw = _load_raw_cookies()
    jar = {}
    for c in raw:
        name = c.get('name', '')
        v    = c.get('value', '')
        if not v or v == 'deleted':
            continue
        if any(name.startswith(pfx) for pfx in _EXCLUDED_COOKIE_PREFIXES):
            continue
        if name in _EXCLUDED_COOKIE_NAMES:
            continue
        jar[name] = v
    n_session = sum(1 for k in jar if k.startswith(('SSESS', 'SHARED_SESSION')))
    print(f"✅ {len(jar)} cookies chargés (dont {n_session} session Drupal/Java)")
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

def _parse_set_cookies(headers) -> dict:
    """Extrait les cookies depuis les headers Set-Cookie d'une réponse curl_cffi."""
    cookies = {}
    # curl_cffi peut regrouper plusieurs Set-Cookie avec \n
    raw = headers.get('set-cookie', '')
    if not raw:
        return cookies
    for line in raw.replace('\r\n', '\n').split('\n'):
        line = line.strip()
        if not line:
            continue
        # Prend uniquement la partie name=value (avant le premier ';')
        kv = line.split(';')[0].strip()
        if '=' in kv:
            name, _, val = kv.partition('=')
            cookies[name.strip()] = val.strip()
    return cookies


async def bypass_queueit(session, html, semaphore, cookie_dict=None):
    """
    Bypass Queue-IT en suivant manuellement la chaîne de redirections.

    Queue-IT page 1 (cookie check) :
        document.location.href = decodeURIComponent('/?c=tenup&...&tsr=...&tsh=...')
    → relative à tenup.queue-it.net.

    On suit chaque redirect manuellement (allow_redirects=False) pour pouvoir
    mettre à jour le header Cookie avec les nouveaux Set-Cookie reçus en chemin.
    Sans cela, le nouveau QueueITAccepted émis par queue-it.net n'est pas
    transmis à tenup.fft.fr → 403.

    Retourne (html_ou_None, live_cookies) — les cookies acquis pendant le bypass
    (nouveau QueueITAccepted, nouveau datadome) doivent être propagés au worker.
    """
    match = re.search(r"decodeURIComponent\('([^']+)'\)", html)
    if not match:
        print("   ⚠️  Queue-IT: pas de decodeURIComponent dans le HTML")
        return None, {}

    decoded = unquote(match.group(1))
    if decoded.startswith('http'):
        current_url = decoded
    elif decoded.startswith('/'):
        current_url = f"https://tenup.queue-it.net{decoded}"
    else:
        current_url = f"https://tenup.queue-it.net/{decoded}"

    print(f"   🔄 Queue-IT bypass → {current_url[:80]}...")

    # Copie des cookies courants (sera enrichi des Set-Cookie de chaque réponse)
    live_cookies = dict(cookie_dict or {})

    async with semaphore:
        for step in range(10):
            headers = dict(HEADERS)
            headers['Referer'] = 'https://tenup.queue-it.net/'
            headers['Cookie'] = '; '.join(f'{k}={v}' for k, v in live_cookies.items())

            try:
                if USE_CURL_CFFI:
                    resp = await session.get(current_url, headers=headers,
                                             timeout=30, allow_redirects=False)
                else:
                    resp = await session.get(current_url, headers=headers,
                                             timeout=30.0, follow_redirects=False)
            except Exception as e:
                print(f"   ⚠️  Queue-IT bypass étape {step+1}: {e}")
                return None

            # Absorbe les nouveaux cookies (Set-Cookie) → mis à jour pour la prochaine requête
            new_cookies = _parse_set_cookies(resp.headers)
            if new_cookies:
                live_cookies.update(new_cookies)

            status = resp.status_code

            if status in (301, 302, 303, 307, 308):
                location = resp.headers.get('location', '')
                if not location:
                    print(f"   ⚠️  Queue-IT: redirect sans Location à l'étape {step+1}")
                    return None, {}
                # Construire URL absolue
                if location.startswith('http'):
                    current_url = location
                elif location.startswith('/'):
                    p = urlparse(current_url)
                    current_url = f"{p.scheme}://{p.netloc}{location}"
                else:
                    current_url = urljoin(current_url, location)
                print(f"   → étape {step+1}: {current_url[:80]}")
                continue

            if status == 200:
                text = resp.text
                if not is_queueit_page(text):
                    print(f"   ✅ Queue-IT contourné ({len(text)} chars) — {current_url[:60]}")
                    return text, live_cookies
                # Encore sur queue-it.net (page JS supplémentaire) ?
                print(f"   ⚠️  Queue-IT: encore sur queue-it.net à l'étape {step+1}")
                return None, {}

            # Log du corps pour diagnostic DataDome
            body_preview = ''
            try:
                body_preview = resp.text[:250]
            except Exception:
                pass
            print(f"   ⚠️  Queue-IT: HTTP {status} à l'étape {step+1} — {current_url[:60]}")
            if body_preview:
                print(f"   🚫 Corps réponse: {body_preview[:200]}")
            return None, {}

        print("   ⚠️  Queue-IT: trop de redirections (>10)")
        return None, {}

# ── Requête HTTP ──────────────────────────────────────────────────
async def fetch_html(session, id_fft, semaphore, dump_html=False, cookie_dict=None):
    """
    Récupère le HTML de la page /classement/{id}/padel.
    Gère automatiquement les challenges Queue-IT.
    Retourne (html_str, status_code, acquired_cookies) ou (None, code, {}) en cas d'échec.

    cookie_dict : si fourni, les cookies sont injectés directement dans le header
                  Cookie (contourne le moteur de cookies de curl_cffi qui peut
                  ne pas envoyer les cookies sans info de domaine).
                  Note : le cookie 'datadome' est exclu intentionnellement pour
                  laisser curl_cffi acquérir son propre cookie DataDome via son
                  fingerprint Chrome (un datadome issu de Playwright causerait un
                  mismatch de fingerprint → 403).

    acquired_cookies : dict des nouveaux cookies (datadome, QueueITAccepted…)
                       obtenus pendant la requête — à propager dans initial_cookies.
    """
    url = f"{TENUP_BASE}/classement/{id_fft}/padel"
    headers = dict(HEADERS)
    headers['Referer'] = random.choice([
        f'{TENUP_BASE}/accueil',
        f'{TENUP_BASE}/recherche/joueurs/padel',
    ])

    # Injection directe des cookies dans le header (contourne curl_cffi cookie engine)
    # datadome exclu : cf. docstring
    if cookie_dict:
        headers['Cookie'] = '; '.join(f'{k}={v}' for k, v in cookie_dict.items()
                                      if k != 'datadome')

    acquired   = {}
    body_403   = None
    final_url  = ''

    async with semaphore:
        try:
            if USE_CURL_CFFI:
                # max_redirects=5 : évite la boucle infinie tenup↔queue-it.net
                # (avec allow_redirects=True sans limite → curl: (47) Max 30 redirects)
                resp = await session.get(url, headers=headers, timeout=30,
                                         allow_redirects=True, max_redirects=5)
            else:
                resp = await session.get(url, headers=headers, timeout=30.0)

            status = resp.status_code
            text   = resp.text if status == 200 else None
            acquired = _parse_set_cookies(resp.headers)
            if status != 200:
                try:
                    body_403  = resp.text[:500]
                    final_url = str(getattr(resp, 'url', '') or '')
                    debug_403_path = os.path.join(os.path.dirname(__file__), 'debug_403.html')
                    with open(debug_403_path, 'w', encoding='utf-8') as _f:
                        _f.write(resp.text)
                except Exception:
                    pass

        except Exception as e:
            return None, str(e), {}

    if text is None:
        if body_403:
            if final_url:
                print(f"   🚫 URL finale après redirects: {final_url}")
            print(f"   🚫 HTTP {status} body preview: {body_403[:300]}")
            print(f"   📄 dump complet → debug_403.html")
        return None, status, {}

    # ── Bypass Queue-IT si nécessaire ────────────────────────────
    if is_queueit_page(text):
        text, bypass_cookies = await bypass_queueit(session, text, semaphore,
                                                    cookie_dict=cookie_dict)
        acquired.update(bypass_cookies)
        if text is None:
            return None, 'queueit_failed', {}
        status = 200

    if dump_html and text:
        with open(DEBUG_HTML, 'w', encoding='utf-8') as f:
            f.write(text)
        print(f"   📄 HTML dumpé dans {DEBUG_HTML} ({len(text)} chars)")

    return text, status, acquired

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
async def worker(worker_id, initial_cookies, semaphore, conn, db_lock, counter, args):
    wid               = f"W{worker_id}"
    pid_str           = f"{os.getpid()}-{worker_id}"
    empty_run         = 0
    consecutive_empty = 0
    dump_html_once    = args.dump_html and worker_id == 0

    # Session partagée par worker (si SHARED_SESSION=True ou httpx)
    if SHARED_SESSION and USE_CURL_CFFI:
        _shared = CurlSession(impersonate="chrome124", cookies=dict(initial_cookies),
                              verify=True, allow_redirects=True)
        worker_session = await _shared.__aenter__()
    elif not USE_CURL_CFFI:
        _shared = httpx.AsyncClient(
            cookies=httpx.Cookies(dict(initial_cookies)),
            follow_redirects=True, http2=True, timeout=30.0,
        )
        worker_session = await _shared.__aenter__()
    else:
        _shared        = None
        worker_session = None  # session fraîche par joueur

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
            # datadome exclu du cookie_dict : le cookie issu de Playwright est lié
            # au fingerprint Playwright → mismatch avec curl_cffi → 403 DataDome.
            # On laisse curl_cffi acquérir son propre datadome via son empreinte Chrome.
            cookie_dict_curl = {k: v for k, v in initial_cookies.items()
                                if k != 'datadome'}

            if USE_CURL_CFFI and not SHARED_SESSION:
                # Session fraîche par joueur + cookies injectés dans le header Cookie
                # (curl_cffi ne transmet pas fiablement les cookies sans info de domaine)
                async with CurlSession(impersonate="chrome124",
                                       verify=True, allow_redirects=True) as fresh_session:
                    html, status, new_cookies = await fetch_html(
                        fresh_session, id_fft, semaphore,
                        dump_html=dump_html_once,
                        cookie_dict=cookie_dict_curl)
            else:
                html, status, new_cookies = await fetch_html(
                    worker_session, id_fft, semaphore,
                    dump_html=dump_html_once,
                    cookie_dict=cookie_dict_curl)

            # Propager les cookies acquis (datadome curl_cffi, nouveau QueueITAccepted)
            if new_cookies:
                for k, v in new_cookies.items():
                    if k and v:
                        if any(k.startswith(pfx) for pfx in _EXCLUDED_COOKIE_PREFIXES):
                            continue
                        if k in _EXCLUDED_COOKIE_NAMES:
                            continue
                        initial_cookies[k] = v
                if 'datadome' in new_cookies:
                    print(f"   🍪 datadome curl_cffi acquis ({new_cookies['datadome'][:30]}...)")
            dump_html_once = False  # une seule fois

            if html is None:
                consecutive_empty += 1
                print(f"   [{wid}] ⚠️  HTTP {status} pour {id_fft} — vide #{consecutive_empty}")

                if status == 'queueit_failed':
                    # Cookie QueueITAccepted expiré → auto-refresh Playwright
                    async with db_lock:
                        conn.execute(
                            "UPDATE scrape_queue SET statut='pending', worker_id=NULL WHERE id_fft=?",
                            (id_fft,)
                        )
                        conn.commit()
                    print(f"   [{wid}] 🍪 Queue-IT bloqué — auto-refresh en cours...")
                    ok = await auto_refresh_cookies_async()
                    if ok:
                        try:
                            new_c = load_cookies_jar()
                            initial_cookies.clear()
                            initial_cookies.update(new_c)
                            consecutive_empty = 0
                            print(f"   [{wid}] ✅ Cookies rechargés — reprise")
                        except Exception as _e:
                            print(f"   [{wid}] ⚠️  Reload cookies : {_e}")
                    else:
                        # Fallback : attente fichier READY_COOKIES (créé manuellement)
                        ready = os.path.join(os.path.dirname(COOKIES_FILE), 'READY_COOKIES')
                        print(f"\n{'='*55}\n🍪 Crée le fichier READY_COOKIES après avoir mis à jour cookies.json\n{'='*55}")
                        while not os.path.exists(ready):
                            await asyncio.sleep(10)
                        os.remove(ready)
                        try:
                            new_c = load_cookies_jar()
                            initial_cookies.clear()
                            initial_cookies.update(new_c)
                            consecutive_empty = 0
                            print(f"   [{wid}] ✅ Cookies rechargés manuellement")
                        except Exception as _e:
                            print(f"   [{wid}] ⚠️  Reload cookies : {_e}")
                    continue

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


    # Libérer la session partagée si elle existe
    if _shared is not None:
        try:
            await _shared.__aexit__(None, None, None)
        except Exception:
            pass

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

    global _refresh_lock
    _refresh_lock = asyncio.Lock()  # init dans la bonne event loop

    counter   = {'scraped': 0}
    db_lock   = asyncio.Lock()
    semaphore = asyncio.Semaphore(n)  # Limite le nb de requêtes simultanées

    mode_str = "fraîche/joueur" if (USE_CURL_CFFI and not SHARED_SESSION) else "partagée/worker"
    print(f"🍪 Mode cookies : {mode_str} (SHARED_SESSION={SHARED_SESSION})")

    # Les workers créent eux-mêmes leurs sessions (fresh par joueur par défaut)
    tasks = [
        asyncio.create_task(
            worker(i, cookies, semaphore, conn, db_lock, counter, args)
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
    parser.add_argument('--shared-session', action='store_true', dest='shared_session',
                        help='Session partagée par worker (legacy, déconseillé)')
    args = parser.parse_args()
    if args.shared_session:
        import scraper_http as _m; _m.SHARED_SESSION = True
    asyncio.run(run_async(args))
