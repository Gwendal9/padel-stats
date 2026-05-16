"""
Tenup Cascade Scraper — Version ASYNC (fast)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Utilise asyncio + Playwright async pour scraper N joueurs en parallèle.
Par défaut : 6 pages simultanées → ~6x plus rapide que la version sync.

Usage :
    python scraper_fast.py                    # 6 workers, 200 joueurs max
    python scraper_fast.py --workers 8        # 8 pages simultanées
    python scraper_fast.py --limit 0          # illimité
    python scraper_fast.py --headless         # mode invisible
    python scraper_fast.py --dump-fiche       # dump le 1er profil JSON dans fiche_debug.json

Multi-process (lancer dans 2-3 terminaux) :
    python scraper_fast.py --workers 6 --limit 5000   (terminal 1)
    python scraper_fast.py --workers 6 --limit 5000   (terminal 2)
    ...
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
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

# ── Config ────────────────────────────────────────────────────────
TENUP_BASE     = 'https://tenup.fft.fr'
COOKIES_FILE   = os.path.join(os.path.dirname(__file__), 'cookies.json')
DB_FILE        = os.path.join(os.path.dirname(__file__), 'tenup.db')
DB_FILE_TEST   = os.path.join(os.path.dirname(__file__), 'tenup_test.db')
STOP_FILE      = os.path.join(os.path.dirname(__file__), 'STOP')
DEBUG_FILE     = os.path.join(os.path.dirname(__file__), 'fiche_debug.json')
SEED_ID        = '7633273415'
API_SEARCH_URL = f'{TENUP_BASE}/back/v1/personnes/joueurs-padel'
BAN_THRESHOLD  = 8

# Délais par worker — équilibre entre vitesse et anti-ban
# Si tu es déjà banni, ces délais ne suffiront pas (faut attendre ou changer d'IP)
DELAY_MIN = 5.0
DELAY_MAX = 8.0
# Toutes les N joueurs, un worker fait une pause plus longue (anti-pattern détection)
LONG_PAUSE_EVERY = 50          # pause tous les 50 joueurs
LONG_PAUSE_MIN   = 20.0
LONG_PAUSE_MAX   = 45.0

# Types de ressources à bloquer (inutiles pour le scraping)
BLOCK_RESOURCE_TYPES = {'image', 'media', 'font', 'stylesheet'}
BLOCK_DOMAINS = (
    'google-analytics', 'googletagmanager', 'facebook', 'hotjar',
    'doubleclick', 'analytics', 'datadome', 'cdn.cookielaw',
    'onetrust', 'clarity.ms', 'crisp.chat',
)

COOKIE_DOMAINS = {
    'SHARED_SESSION_JAVA':                     'tenup.fft.fr',
    'SSESS7ba44afc36c80c3faa2b8fa87e7742c5':   '.fft.fr',
    'datadome':                                '.fft.fr',
    'QueueITAccepted-SDFrts345E-V3_tenupprod': 'tenup.fft.fr',
}

# ── Cookies / Session ─────────────────────────────────────────────
SAMESIDE_MAP = {
    'no_restriction': 'None',
    'unspecified':    'None',
    'lax':            'Lax',
    'strict':         'Strict',
    'none':           'None',
}

def _load_raw_cookies():
    """Charge cookies.json — accepte le format Cookie-Editor (liste) ou l'ancien format (dict)."""
    if not os.path.exists(COOKIES_FILE):
        raise FileNotFoundError("cookies.json introuvable")
    with open(COOKIES_FILE) as f:
        data = json.load(f)
    # Ancien format : dict plat {"nom": "valeur"}
    if isinstance(data, dict):
        return [{'name': k, 'value': v, 'domain': COOKIE_DOMAINS.get(k, 'tenup.fft.fr'), 'path': '/'}
                for k, v in data.items() if v and not v.startswith('COLLE_')]
    # Nouveau format Cookie-Editor : liste d'objets
    return data

def load_cookies():
    """Retourne les cookies au format Playwright (add_cookies).
    On ne passe que name/value/domain/path — les autres champs (sameSite, httpOnly, secure)
    peuvent être rejetés silencieusement par Playwright selon la version.
    """
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
    # Migrations silencieuses
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
        # Force la remise en pending même si déjà en base (utile pour --seed et --test)
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

def reset_stuck_processing(conn, timeout_minutes=30):
    """
    Remet en pending les joueurs bloqués en 'processing' depuis plus de N minutes.
    Utilise processing_at (quand le scraping a commencé) et non added_at (quand ajouté en queue).
    """
    cutoff = (datetime.now() - timedelta(minutes=timeout_minutes)).isoformat()
    # Cas 1 : processing_at renseigné et trop vieux
    reset1 = conn.execute(
        "UPDATE scrape_queue SET statut='pending', worker_id=NULL "
        "WHERE statut='processing' AND processing_at IS NOT NULL AND processing_at < ?",
        (cutoff,)
    ).rowcount
    # Cas 2 : processing_at absent (anciens enregistrements) → fallback sur added_at
    reset2 = conn.execute(
        "UPDATE scrape_queue SET statut='pending', worker_id=NULL "
        "WHERE statut='processing' AND processing_at IS NULL AND added_at < ?",
        (cutoff,)
    ).rowcount
    conn.commit()
    total = reset1 + reset2
    if total:
        print(f"♻️  {total} joueurs bloqués remis en pending")

MAX_RETRIES = 3  # Nombre max de tentatives avant de marquer comme error

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

# ── Recherche partenaire ──────────────────────────────────────────
def parse_partner_name(full_name):
    parts        = full_name.strip().split()
    nom_parts    = [p for p in parts if p == p.upper() and p.isalpha()]
    prenom_parts = [p for p in parts if p not in nom_parts]
    if not nom_parts or not prenom_parts:
        if len(parts) >= 2:
            prenom_parts = parts[:-1]
            nom_parts    = parts[-1:]
        else:
            return None, None
    return ' '.join(nom_parts), ' '.join(prenom_parts)

async def search_joueur_padel(context, partenaire_nom):
    """Recherche l'ID FFT d'un partenaire via context.request Playwright.
    Nécessite que /recherche/joueurs/padel ait été chargé avant (warm-up).
    Essaie les deux ordres : 'NOM Prénom' et 'Prénom NOM'."""
    if not partenaire_nom or not partenaire_nom.strip():
        return None
    parts = partenaire_nom.strip().split()
    if len(parts) < 2:
        return None

    candidats = [
        (parts[-1], ' '.join(parts[:-1])),   # dernier = prénom, reste = nom
        (parts[0],  ' '.join(parts[1:])),     # premier = prénom, reste = nom
    ]
    if candidats[0] == candidats[1]:
        candidats = [candidats[0]]

    for prenom, nom in candidats:
        if not prenom or not nom:
            continue
        try:
            resp = await context.request.post(
                API_SEARCH_URL,
                data=json.dumps({"from": 0, "size": 5, "nom": nom, "prenom": prenom}),
                headers={
                    'Content-Type': 'application/json',
                    'Accept': 'application/json, text/plain, */*',
                    'Referer': f'{TENUP_BASE}/recherche/joueurs/padel',
                    'Origin': TENUP_BASE,
                }
            )
            if resp.status == 200:
                data = await resp.json()
                joueurs = data.get('joueurs', [])
                if joueurs:
                    return str(joueurs[0]['idCrm'])
            elif resp.status == 401:
                return '401'
        except Exception as e:
            print(f"   ⚠️  API erreur: {e}")
        await asyncio.sleep(0.1)
    return None

# ── Playwright async ──────────────────────────────────────────────
async def fetch_profile_async(context, id_fft):
    """
    Charge la page joueur en async avec :
    - Blocage des ressources inutiles (images, fonts, CSS) — appliqué par PAGE
    - Interception des réponses XHR spécifiques au joueur (filtrées par id_fft dans l'URL)
    - Attente intelligente de la table des tournois
    """
    url         = f"{TENUP_BASE}/classement/{id_fft}/padel"
    intercepted = {'fiche': None, 'raw_responses': []}

    page = await context.new_page()

    # ── Route par page (pas par contexte — sinon ça interfère entre workers) ──
    async def _route_handler(route):
        req = route.request
        if req.resource_type in BLOCK_RESOURCE_TYPES:
            await route.abort()
            return
        if any(d in req.url for d in BLOCK_DOMAINS):
            await route.abort()
            return
        await route.continue_()

    async def _on_response(response):
        try:
            ct = response.headers.get('content-type', '')
            if 'json' not in ct:
                return
            url_r = response.url
            try:
                data = await response.json()
                if not isinstance(data, dict):
                    return

                # Toujours enregistrer pour le debug
                intercepted['raw_responses'].append({
                    'url':  url_r,
                    'keys': list(data.keys()),
                    'data': data,
                })

                # La fiche joueur contient rangsParPratique au niveau racine.
                # On ne peut PAS exiger nom/prenom truthy : les joueurs anonymes
                # ont nom=null/prenom=null mais sont des fiches valides.
                # Heuristique : présence de rangsParPratique + au moins une autre clé
                # caractéristique d'une fiche (et pas la home /connecte/site).
                if (intercepted['fiche'] is None
                        and 'rangsParPratique' in data
                        and 'profil' not in data  # exclut la home utilisateur
                        and 'agenda' not in data):
                    intercepted['fiche'] = data
                    intercepted['fiche_url'] = url_r

            except:
                pass
        except:
            pass

    await page.route('**/*', _route_handler)
    page.on('response', _on_response)

    try:
        await page.goto(url, wait_until='domcontentloaded', timeout=60000)

        if 'queue' in page.url.lower() or 'queue-it' in page.url.lower():
            print(f"   ⏳ Queue-it détecté ({id_fft}) — attente retour...")
            try:
                await page.wait_for_url(f'**/classement/{id_fft}/**', timeout=120000)
            except:
                pass

        # Stratégie rapide : on essaie de lire le profil dès domcontentloaded
        # Le site est SSR (Nuxt) — la fiche est dans le HTML initial.
        # On évite networkidle (jusqu'à 12s) qui est inutile pour le parsing.
        html = await page.content()
        profile_check = parse_profile(html, id_fft, {'fiche': None, 'raw_responses': []})

        if not profile_check.get('nom'):
            # Données pas encore là → attendre un peu le rendu Vue.js
            try:
                await page.wait_for_load_state('networkidle', timeout=7000)
            except:
                await asyncio.sleep(2)
            html = await page.content()

    finally:
        try:
            page.remove_listener('response', _on_response)
        except:
            pass
        try:
            await page.unroute('**/*', _route_handler)
        except:
            pass

    return html, intercepted, page

# ── Parser ────────────────────────────────────────────────────────
# Flag global pour le debug (dump une seule fois)
_debug_dumped = False

def parse_profile(html, id_fft, intercepted, dump_fiche=False):
    global _debug_dumped

    profile = {
        'id_fft': id_fft,
        'nom': '', 'prenom': '', 'ville': '',
        'echelon': '', 'classement': None, 'sexe': '', 'naissance': '',
        'club_nom': '',
        'meilleur_classement': None,
        'variation_classement': None,   # delta mensuel : rang actuel - rang mois précédent
        'tournaments': []
    }

    # ── Priorité 1 : fiche interceptée via XHR ──────────────────────
    fiche = intercepted.get('fiche') or {}

    # ── Priorité 2 : fiche embarquée dans le HTML ───────────────────
    if not fiche:
        for marker in ('"fft_fiche_joueur"', '"ficheJoueur"', '"profil"'):
            idx = html.find(marker)
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
                fiche = json.loads(html[start:end])
                # Accepter si nom/prenom OU rangsParPratique (= joueur anonyme)
                if (fiche.get('nom') or fiche.get('prenom')
                        or 'rangsParPratique' in fiche
                        or fiche.get('dernierClassementPadel')):
                    break
                fiche = {}
            except:
                fiche = {}

    # ── Debug : dump complet pour diagnostiquer les clés manquantes ──
    if dump_fiche and not _debug_dumped:
        _debug_dumped = True
        raw = intercepted.get('raw_responses', [])
        debug_out = {
            'id_fft':       id_fft,
            'fiche_url':    intercepted.get('fiche_url', 'non trouvée via XHR'),
            'fiche_found':  fiche is not None and bool(fiche),
            'full_fiche':   fiche,
            'all_xhr_responses': [
                {'url': r['url'], 'top_keys': r.get('keys', [])}
                for r in raw
            ],
            # Toutes les réponses complètes pour investigation
            'xhr_full_data': raw,
        }
        with open(DEBUG_FILE, 'w', encoding='utf-8') as f:
            json.dump(debug_out, f, ensure_ascii=False, indent=2)

        print(f"\n{'─'*60}")
        print(f"   📄 DEBUG → {DEBUG_FILE}")
        print(f"   Fiche trouvée via XHR : {intercepted.get('fiche_url', '❌ NON')}")
        print(f"   Fiche trouvée via HTML : {'✅' if fiche else '❌'}")
        print(f"\n   Toutes les requêtes XHR JSON ({len(raw)}) :")
        for r in raw:
            marker = " ← FICHE" if r['url'] == intercepted.get('fiche_url') else ""
            print(f"      {r['url'][:90]}{marker}")
            print(f"         clés : {r.get('keys', [])}")
        if fiche:
            print(f"\n   Contenu fiche :")
            for k, v in fiche.items():
                if v and not isinstance(v, (dict, list)):
                    print(f"      {k!r}: {str(v)[:100]!r}")
        print(f"{'─'*60}")

    if fiche:
        try:
            profile['nom']       = fiche.get('nom', '')
            profile['prenom']    = fiche.get('prenom', '')
            profile['ville']     = fiche.get('ville', fiche.get('commune', ''))
            profile['sexe']      = fiche.get('sexe', fiche.get('genre', ''))
            profile['naissance'] = fiche.get('birthYear', fiche.get('anneeNaissance', fiche.get('dateNaissance', '')))

            # ── Échelon : classementTennis.dernierClassement.echelon ────
            # Format réel : {"classementTennis": {"dernierClassement": {"echelon": 60, ...}}}
            ct = fiche.get('classementTennis') or {}
            dc = ct.get('dernierClassement') or {}
            if dc.get('echelon'):
                profile['echelon'] = str(dc['echelon'])
            elif fiche.get('echelon'):
                # Fallback si la structure change un jour
                profile['echelon'] = str(fiche['echelon'])

            # ── Club : fiche['club']['nom'] ──────────────────────────────
            # Format réel : {"club": {"nom": "TC LES LILAS", "code": "...", ...}}
            club_obj = fiche.get('club')
            if isinstance(club_obj, dict):
                profile['club_nom'] = (club_obj.get('nom') or '').strip()
            elif isinstance(club_obj, str) and club_obj.strip():
                profile['club_nom'] = club_obj.strip()
            # Fallbacks si la structure diffère
            if not profile['club_nom']:
                # 'nomClubRattachement' = clé du HTML embarqué (format principal)
                for ck in ('nomClubRattachement', 'nomClub', 'libelleClub', 'clubNom',
                           'structureLibelle', 'nomStructure', 'libelleCentre'):
                    cv = fiche.get(ck)
                    if cv and isinstance(cv, str) and cv.strip():
                        profile['club_nom'] = cv.strip()
                        break

            # ── Classement padel ─────────────────────────────────────────
            # Format HTML  : fiche['dernierClassementPadel'] = '14335'
            # Format XHR   : fiche['rangsParPratique'][0]['dernierRang'] = 14335
            for cl_key in ('dernierClassementPadel', 'classementPadel'):
                cl_val = fiche.get(cl_key)
                if cl_val and cl_val != 'NC':
                    try:
                        profile['classement'] = int(cl_val)
                        break
                    except (ValueError, TypeError):
                        pass
            if profile['classement'] is None:
                for rang_entry in (fiche.get('rangsParPratique') or []):
                    if (rang_entry.get('pratique') or {}).get('code', '').upper() == 'PADEL':
                        try:
                            profile['classement'] = int(rang_entry['dernierRang'])
                        except (KeyError, TypeError, ValueError):
                            pass
                        break
            if profile['classement'] is None:
                for cl_key in ('classement', 'rang', 'ranking', 'rangNational'):
                    cl_val = fiche.get(cl_key)
                    if cl_val is not None and cl_val != 'NC':
                        try:
                            profile['classement'] = int(cl_val)
                            break
                        except (ValueError, TypeError):
                            pass

            # ── Meilleur classement padel ────────────────────────────────
            # Format HTML : fiche['meilleurClassementPadel'] = '12655'
            # Format XHR  : fiche['rangsParPratique'][0]['meilleurRang']
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

            # ── Variation mensuelle de classement ────────────────────────
            # La FFT publie ce delta le 1er mardi du mois (ex: -491 = perdu 491 places)
            # Clés connues dans rangsParPratique : evolutionRang, evolution, nbEvolution
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
            # Fallback : clés directes sur la fiche
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

        # ── Joueur anonymisé : nom caché mais classement public ─────────
        # Si on a trouvé une fiche (= le serveur a bien répondu) mais sans nom,
        # c'est un joueur qui a masqué son identité. On le sauvegarde quand même.
        if not profile['nom'] and (
            profile['classement'] is not None
            or profile['meilleur_classement'] is not None
        ):
            profile['nom'] = 'Anonyme'

    # ── Table des tournois (HTML) ────────────────────────────────────
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

def save_profile(conn, profile):
    now = datetime.now().isoformat()
    # Mois courant au format YYYY-MM (ex: "2026-05") pour l'historique
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

    # ── Snapshot mensuel dans classements_historique ─────────────────
    # Idempotent : si le snapshot de ce mois existe déjà, on le met à jour
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
            pass  # Table pas encore migrée (mode old DB)

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

# ── Worker async ──────────────────────────────────────────────────
async def worker(worker_id, context, conn, db_lock, counter, args):
    """
    Un worker = une tab Playwright indépendante.
    Tourne en boucle : claim un joueur → scrape → save → claim suivant.
    Plusieurs workers s'exécutent en parallèle dans le même event loop.
    """
    wid                = f"W{worker_id}"
    pid_str            = f"{os.getpid()}-{worker_id}"
    empty_run          = 0
    consecutive_empty  = 0   # compteur PER WORKER (pas partagé)
    api_calls_since_warmup = 0  # renouveler le warm-up périodiquement
    scraped_by_worker  = 0   # pour déclencher les longues pauses périodiques

    # Warm-up initial : établir la session API sur /recherche/joueurs/padel
    # (cette page authentifie context.request, contrairement à /classement/...)
    _wp = await context.new_page()
    try:
        await _wp.goto(f'{TENUP_BASE}/recherche/joueurs/padel',
                       wait_until='domcontentloaded', timeout=30000)
        await asyncio.sleep(1)
    except Exception:
        pass
    finally:
        await _wp.close()

    while True:
        # ── Arrêt sur fichier STOP ───────────────────────────────────
        if os.path.exists(STOP_FILE):
            print(f"   [{wid}] ⛔ STOP détecté — arrêt.")
            break

        # ── Limite globale ───────────────────────────────────────────
        if args.limit > 0 and counter['scraped'] >= args.limit:
            break

        # ── Claim joueur suivant (thread-safe via db_lock) ───────────
        async with db_lock:
            id_fft = await asyncio.to_thread(get_next, conn, pid_str)

        if not id_fft:
            # Queue vide : attendre un peu (d'autres workers ajoutent peut-être des partenaires)
            empty_run += 1
            if empty_run >= 3:
                print(f"   [{wid}] ✅ Queue vide.")
                break
            await asyncio.sleep(5)
            continue

        empty_run = 0

        async with db_lock:
            total, done, pending, processing, errors, joueurs, parts = await asyncio.to_thread(stats, conn)

        print(f"[{done+1}/{total}] [{wid}] 🎭 {id_fft} — "
              f"pending:{pending} done:{done} | joueurs:{joueurs} parts:{parts}")

        page = None
        try:
            # ── Fetch ────────────────────────────────────────────────
            html, intercepted, page = await fetch_profile_async(context, id_fft)
            profile = parse_profile(html, id_fft, intercepted, dump_fiche=args.dump_fiche)

            if profile['nom']:
                club_str  = f" | club:{profile['club_nom']}" if profile['club_nom'] else ""
                best      = profile.get('meilleur_classement')
                curr      = profile.get('classement')
                rank_str  = f" | #{curr}" if curr else ""
                rank_str += f" (best #{best})" if best and best != curr else ""
                anon_tag  = " 🕵️ ANONYME" if profile['nom'] == 'Anonyme' else ""
                print(f"   [{wid}] 👤 {profile['prenom']} {profile['nom']}{anon_tag}"
                      f" — {len(profile['tournaments'])} tournois{club_str}{rank_str}")
                consecutive_empty = 0
            else:
                # Vérifier le nb de tentatives pour ce joueur spécifique
                retries_done = conn.execute(
                    "SELECT retries FROM scrape_queue WHERE id_fft=?", (id_fft,)
                ).fetchone()
                retries_done = retries_done[0] if retries_done else 1
                # Ne compter QUE les premières tentatives dans le détecteur de ban
                # (les retries d'un même profil mort polluent sinon le compteur)
                if retries_done <= 1:
                    consecutive_empty += 1
                print(f"   [{wid}] ⚠️  Profil vide — tentative {retries_done}/{MAX_RETRIES}"
                      f" (signal ban: {consecutive_empty}/{BAN_THRESHOLD})")

                if retries_done >= MAX_RETRIES:
                    # Trop de tentatives : marquer comme erreur définitive
                    async with db_lock:
                        await asyncio.to_thread(mark_error, conn, id_fft, f"profil vide après {retries_done} tentatives")
                    counter['scraped'] += 1
                    # Profil mort confirmé → ne plus polluer le compteur de ban
                    consecutive_empty = max(0, consecutive_empty - 1)
                    continue

                # Ban détecté → cooldown long puis warm-up, pas un STOP global
                if consecutive_empty >= BAN_THRESHOLD:
                    print(f"\n🚨 [{wid}] {BAN_THRESHOLD} profils vides consécutifs — cooldown 10min + warm-up")
                    async with db_lock:
                        conn.execute(
                            "UPDATE scrape_queue SET statut='pending', worker_id=NULL WHERE id_fft=?",
                            (id_fft,)
                        )
                        conn.commit()
                    # Cooldown : 10 minutes pour laisser DataDome se calmer
                    await asyncio.sleep(600)
                    # Re-warm-up de la session
                    try:
                        _rwp = await context.new_page()
                        await _rwp.goto(f'{TENUP_BASE}/recherche/joueurs/padel',
                                        wait_until='domcontentloaded', timeout=30000)
                        await asyncio.sleep(2)
                        await _rwp.close()
                    except Exception:
                        pass
                    consecutive_empty = 0
                    print(f"   [{wid}] ▶️  reprise après cooldown")
                    continue

                # Profil vide mais pas encore au max de tentatives :
                # remettre en pending pour réessayer plus tard
                async with db_lock:
                    conn.execute(
                        "UPDATE scrape_queue SET statut='pending', worker_id=NULL WHERE id_fft=?",
                        (id_fft,)
                    )
                    conn.commit()
                continue  # ← ne pas tomber dans save/mark_done

            # ── Résolution IDs partenaires (via context.request) ────────
            partners_to_add = []
            need_rewarmup = False
            for t in profile['tournaments']:
                if t['partenaire_nom'] and not t['partenaire_id']:
                    pid = await search_joueur_padel(context, t['partenaire_nom'])
                    if pid == '401':
                        need_rewarmup = True
                        break
                    if pid:
                        t['partenaire_id'] = pid
                        partners_to_add.append(pid)
                    await asyncio.sleep(0.3)
                elif t['partenaire_id']:
                    partners_to_add.append(t['partenaire_id'])

            # Re-warm-up si la session API a expiré (page classement la casse)
            if need_rewarmup:
                _rw = await context.new_page()
                try:
                    await _rw.goto(f'{TENUP_BASE}/recherche/joueurs/padel',
                                   wait_until='domcontentloaded', timeout=30000)
                    await asyncio.sleep(1)
                except Exception:
                    pass
                finally:
                    await _rw.close()

            # ── Sauvegarde (sérialisée) ──────────────────────────────
            async with db_lock:
                await asyncio.to_thread(save_profile, conn, profile)

                added = 0
                seen  = set()
                for pid in partners_to_add:
                    if pid and pid != id_fft and pid not in seen:
                        seen.add(pid)
                        # Vérifier à la fois en queue ET en joueurs (sinon redécouverte après reset)
                        in_queue = conn.execute(
                            "SELECT COUNT(*) FROM scrape_queue WHERE id_fft=?", (pid,)
                        ).fetchone()[0]
                        in_joueurs = conn.execute(
                            "SELECT COUNT(*) FROM joueurs WHERE id_fft=?", (pid,)
                        ).fetchone()[0]
                        if not in_queue and not in_joueurs:
                            add_to_queue(conn, pid)
                            added += 1

                await asyncio.to_thread(mark_done, conn, id_fft)

            if added:
                print(f"   [{wid}] ➕ {added} nouveaux partenaires")

            counter['scraped'] += 1
            scraped_by_worker += 1

        except Exception as e:
            print(f"   [{wid}] ❌ Erreur {id_fft}: {e}")
            async with db_lock:
                await asyncio.to_thread(mark_error, conn, id_fft, e)
        finally:
            if page:
                try:
                    await page.close()
                except:
                    pass
            page = None

        # ── Délai anti-détection par worker ─────────────────────────
        # Pause longue périodique pour casser le pattern robotique
        if scraped_by_worker > 0 and scraped_by_worker % LONG_PAUSE_EVERY == 0:
            long_delay = random.uniform(LONG_PAUSE_MIN, LONG_PAUSE_MAX)
            print(f"   [{wid}] 💤 pause longue {long_delay:.0f}s (anti-détection, {scraped_by_worker} scrapés)")
            await asyncio.sleep(long_delay)
        else:
            delay = random.uniform(DELAY_MIN, DELAY_MAX)
            await asyncio.sleep(delay)

    # Nettoyer les joueurs qu'on avait claim
    async with db_lock:
        conn.execute(
            "UPDATE scrape_queue SET statut='pending', worker_id=NULL "
            "WHERE statut='processing' AND worker_id=?",
            (pid_str,)
        )
        conn.commit()

# ── Point d'entrée ────────────────────────────────────────────────
async def run_async(args):
    n        = args.workers
    db_path  = DB_FILE_TEST if args.test else DB_FILE
    seed     = args.seed or SEED_ID

    print(f"🎾 Tenup Fast Scraper — {n} workers, limite: {args.limit if args.limit > 0 else '∞'}")
    print(f"📁 Base : {db_path}" + (" ⚠️  MODE TEST" if args.test else ""))
    print(f"🖥️  Mode : {'headless' if args.headless else 'visible'}")
    if args.dump_fiche:
        print(f"🔍 Mode debug : premier profil dumpé dans {DEBUG_FILE}")

    cookies = load_cookies()
    conn    = init_db(db_path)

    # Si --seed explicite ou --test : forcer la remise en pending
    add_to_queue(conn, seed, force=(args.test or args.seed is not None))
    reset_stuck_processing(conn)

    if os.path.exists(STOP_FILE):
        print(f"⛔ Fichier STOP existant — supprime-le d'abord : rm STOP")
        return

    # Compteurs partagés entre workers
    counter = {'scraped': 0, 'empty': 0}
    db_lock = asyncio.Lock()

    async with async_playwright() as p:
        use_cdp = getattr(args, 'cdp', False)
        if use_cdp:
            # Connexion à un Chrome déjà ouvert par l'utilisateur (port 9222).
            # → Hérite de la session, des cookies, et surtout du fingerprint réel
            # qui est validé par DataDome. C'est la solution bulletproof.
            print("🔌 Connexion à Chrome via CDP (localhost:9222)...")
            browser = await p.chromium.connect_over_cdp('http://localhost:9222')
            # En CDP, on récupère le contexte par défaut (le navigateur de l'user)
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            print(f"✅ Connecté ({len(browser.contexts)} contexts, {len(context.pages)} pages existantes)")
        else:
            # Mode normal : Playwright lance son propre Chrome
            try:
                browser = await p.chromium.launch(
                    headless=args.headless,
                    channel='chrome',
                    args=[
                        '--disable-blink-features=AutomationControlled',
                        '--disable-web-security',
                        '--no-sandbox',
                        '--disable-features=IsolateOrigins,site-per-process',
                        '--disable-infobars',
                    ]
                )
                print("✅ Chrome système utilisé")
            except Exception as e:
                print(f"⚠️  Chrome système indisponible ({e}) — fallback Chromium")
                browser = await p.chromium.launch(
                    headless=args.headless,
                    args=[
                        '--disable-blink-features=AutomationControlled',
                        '--disable-web-security',
                        '--no-sandbox',
                        '--disable-features=IsolateOrigins,site-per-process',
                        '--disable-infobars',
                    ]
                )
            context = await browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36',
                locale='fr-FR',
                viewport={'width': 1920, 'height': 1080},
                timezone_id='Europe/Paris',
                extra_http_headers={
                    'Accept-Language': 'fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7',
                    'sec-ch-ua': '"Chromium";v="137", "Not/A)Brand";v="24", "Google Chrome";v="137"',
                    'sec-ch-ua-mobile': '?0',
                    'sec-ch-ua-platform': '"Windows"',
                },
            )

            # Masquer les signaux d'automation (navigator.webdriver, plugins, etc.)
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [
                        { name: 'PDF Viewer', filename: 'internal-pdf-viewer' },
                        { name: 'Chrome PDF Viewer', filename: 'internal-pdf-viewer' },
                        { name: 'Chromium PDF Viewer', filename: 'internal-pdf-viewer' },
                    ],
                });
                Object.defineProperty(navigator, 'languages', { get: () => ['fr-FR', 'fr', 'en-US', 'en'] });
                window.chrome = { runtime: {} };
                const origQuery = window.navigator.permissions && window.navigator.permissions.query;
                if (origQuery) {
                    window.navigator.permissions.query = (params) => (
                        params && params.name === 'notifications'
                            ? Promise.resolve({ state: Notification.permission })
                            : origQuery(params)
                    );
                }
            """)
            await context.add_cookies(cookies)

        try:
            tasks = [
                asyncio.create_task(
                    worker(i, context, conn, db_lock, counter, args)
                )
                for i in range(n)
            ]
            await asyncio.gather(*tasks)
        finally:
            # En mode CDP : on ne ferme PAS le navigateur (c'est celui de l'user)
            if not use_cdp:
                try: await context.close()
                except: pass
                try: await browser.close()
                except: pass

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
    parser = argparse.ArgumentParser(description='Tenup Fast Scraper (async)')
    parser.add_argument('--workers',    type=int,  default=3,
                        help='Nombre de pages Playwright en parallèle (défaut: 3 — safe anti-DataDome)')
    parser.add_argument('--limit',      type=int,  default=200,
                        help='Joueurs max ce run (0=illimité)')
    parser.add_argument('--headless',   action='store_true',
                        help='Navigateur invisible')
    parser.add_argument('--dump-fiche', action='store_true',
                        help='Dump le 1er profil JSON dans fiche_debug.json pour diagnostiquer')
    parser.add_argument('--test',       action='store_true',
                        help='Mode test (DB séparée tenup_test.db)')
    parser.add_argument('--seed',       type=str,  default=None,
                        help='ID FFT de départ (défaut: 7633273415)')
    parser.add_argument('--cdp',        action='store_true',
                        help='Se connecter à un Chrome déjà ouvert (port 9222) au lieu de lancer Playwright. '
                             'Bypass DataDome car utilise ta vraie session navigateur.')
    args = parser.parse_args()
    asyncio.run(run_async(args))
