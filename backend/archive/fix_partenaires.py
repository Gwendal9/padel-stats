"""
fix_partenaires.py
------------------
Rattrapage des partenaires manquants via l'API FFT.

Optimisation : travaille sur les noms UNIQUES (pas sur chaque ligne).
Apres enrich_partenaires_local.py, il reste ~7k noms sans match local.
Ce script les resout via l'API FFT puis met a jour toutes les lignes
correspondantes en une seule passe SQL.

Usage :
    python fix_partenaires.py                   # resout tous les noms manquants
    python fix_partenaires.py --dry-run         # affiche sans modifier la DB
    python fix_partenaires.py --limit 200       # traite 200 noms max (test)
    python fix_partenaires.py --visible         # browser visible pour debug
"""

import asyncio
import argparse
import json
import os
import sqlite3

from playwright.async_api import async_playwright

# -- Config ----------------------------------------------------------------
BASE_DIR       = os.path.dirname(__file__)
DB_FILE        = os.path.join(BASE_DIR, 'tenup.db')
COOKIES_FILE   = os.path.join(BASE_DIR, 'cookies.json')
TENUP_BASE     = 'https://tenup.fft.fr'
API_SEARCH_URL = f'{TENUP_BASE}/back/v1/personnes/joueurs-padel'

ANON_NAMES = {"joueur anonyme", "anonyme", "joueur anon", "inconnu"}

# -- Cookies ---------------------------------------------------------------
def load_cookies():
    with open(COOKIES_FILE, 'r', encoding='utf-8') as f:
        raw = json.load(f)
    out = []
    for c in raw:
        pw = {
            'name':     c['name'],
            'value':    c.get('value', ''),
            'domain':   c.get('domain', 'tenup.fft.fr'),
            'path':     c.get('path', '/'),
            'secure':   c.get('secure', False),
            'httpOnly': c.get('httpOnly', False),
        }
        if c.get('expirationDate'):
            pw['expires'] = int(c['expirationDate'])
        out.append(pw)
    return out

# -- API -------------------------------------------------------------------
async def _api_post(context, nom, prenom):
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
            return data.get('joueurs', []), None
        elif resp.status == 401:
            return None, '401'
    except Exception as e:
        print(f"   Warning API: {e}")
    return None, None

async def resolve_name(context, partenaire_nom):
    """
    Essaie les deux ordres NOM/Prenom.
    Retourne l'id_fft si exactement 1 resultat, None si 0, '401' si auth morte.
    """
    parts = partenaire_nom.strip().split()
    if len(parts) < 2:
        return None

    candidats = [
        (parts[-1], ' '.join(parts[:-1])),   # dernier mot = prenom
        (parts[0],  ' '.join(parts[1:])),     # premier mot = prenom
    ]
    if candidats[0] == candidats[1]:
        candidats = [candidats[0]]

    found_id = None
    for prenom, nom in candidats:
        if not prenom or not nom:
            continue
        joueurs, err = await _api_post(context, nom, prenom)
        if err == '401':
            return '401'
        if joueurs:
            candidate_id = str(joueurs[0]['idCrm'])
            if found_id and found_id != candidate_id:
                # Les deux ordres donnent des IDs differents : ambigu, on skip
                return None
            found_id = candidate_id
        await asyncio.sleep(0.1)

    return found_id

# -- DB helpers ------------------------------------------------------------
def get_unique_missing_names(conn, limit=0):
    """Retourne les noms uniques sans partenaire_id (hors anonymes)."""
    q = """
        SELECT DISTINCT partenaire_nom
        FROM participations
        WHERE (partenaire_id IS NULL OR partenaire_id = '')
          AND partenaire_nom IS NOT NULL
          AND partenaire_nom != ''
        ORDER BY partenaire_nom
    """
    if limit > 0:
        q += f" LIMIT {limit}"
    names = [r[0] for r in conn.execute(q).fetchall()]
    return [n for n in names if n.strip().lower() not in ANON_NAMES]

def apply_resolved(conn, name_to_id: dict):
    """Met a jour toutes les lignes pour chaque nom resolu (une requete par nom)."""
    total = 0
    conn.execute("BEGIN")
    for nom, id_fft in name_to_id.items():
        conn.execute(
            "UPDATE participations SET partenaire_id=? WHERE partenaire_nom=? AND (partenaire_id IS NULL OR partenaire_id='')",
            (id_fft, nom)
        )
        total += conn.execute("SELECT changes()").fetchone()[0]
    conn.execute("COMMIT")
    return total

def add_unknowns_to_queue(conn, ids: set):
    """Ajoute a scrape_queue les joueurs pas encore en base."""
    added = 0
    for id_fft in ids:
        already = conn.execute(
            "SELECT 1 FROM joueurs WHERE id_fft=? UNION SELECT 1 FROM scrape_queue WHERE id_fft=?",
            (id_fft, id_fft)
        ).fetchone()
        if not already:
            conn.execute(
                "INSERT OR IGNORE INTO scrape_queue (id_fft, statut, added_at) VALUES (?, 'pending', datetime('now'))",
                (id_fft,)
            )
            added += 1
    conn.commit()
    return added

# -- Main ------------------------------------------------------------------
async def main():
    parser = argparse.ArgumentParser(description='Rattrapage partenaires via API FFT')
    parser.add_argument('--dry-run', action='store_true', help='Affiche sans modifier la DB')
    parser.add_argument('--limit',   type=int, default=0,  help='Nb max de noms uniques a traiter')
    parser.add_argument('--visible', action='store_true',  help='Browser visible')
    args = parser.parse_args()

    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA journal_mode=WAL")

    names = get_unique_missing_names(conn, limit=args.limit)
    total = len(names)
    print(f"Noms uniques a resoudre via API : {total:,}")
    if total == 0:
        print("Rien a faire !")
        conn.close()
        return
    if args.dry_run:
        print("Mode dry-run -- aucune modification\n")

    cookies = load_cookies()

    resolved   = {}   # nom -> id_fft
    not_found  = 0
    errors     = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=not args.visible,
            args=['--disable-blink-features=AutomationControlled', '--no-sandbox']
        )
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            locale='fr-FR',
        )
        await context.add_cookies(cookies)

        # Etablir la session via une vraie page (contourne Queue-IT)
        print("Chargement page pour etablir la session...")
        page = await context.new_page()
        try:
            await page.goto(f'{TENUP_BASE}/recherche/joueurs/padel',
                            wait_until='domcontentloaded', timeout=60000)
            import asyncio as _a; await _a.sleep(3)
            print(f"   URL : {page.url}")
        except Exception as e:
            print(f"   Warning navigation: {e}")
        finally:
            await page.close()

        # Test auth
        print("Verification auth API...")
        resp = await context.request.post(
            API_SEARCH_URL,
            data=json.dumps({"from": 0, "size": 3, "nom": "ROLLAND", "prenom": "Gwendal"}),
            headers={'Content-Type': 'application/json', 'Accept': 'application/json, text/plain, */*',
                     'Referer': f'{TENUP_BASE}/recherche/joueurs/padel', 'Origin': TENUP_BASE}
        )
        print(f"   Status : {resp.status}")
        if resp.status != 200:
            print("Auth invalide -- rafraichir cookies.json et relancer.")
            await browser.close()
            conn.close()
            return
        print("Auth OK\n")

        # Resolution des noms uniques
        for i, nom in enumerate(names):
            pid = await resolve_name(context, nom)

            if pid == '401':
                print(f"\nAPI 401 au nom #{i+1} -- rafraichir cookies et relancer.")
                errors += 1
                break
            elif pid:
                resolved[nom] = pid
            else:
                not_found += 1

            if (i + 1) % 100 == 0:
                pct = (i + 1) / total * 100
                print(f"   [{i+1}/{total}] {pct:.1f}% -- resolus: {len(resolved)} | introuvables: {not_found}")

            await asyncio.sleep(0.15)

        await context.close()
        await browser.close()

    # Application en base
    rows_updated = 0
    added_queue  = 0
    if resolved and not args.dry_run:
        print(f"\nApplication de {len(resolved)} noms resolus...")
        rows_updated = apply_resolved(conn, resolved)
        added_queue  = add_unknowns_to_queue(conn, set(resolved.values()))

    conn.close()

    print(f"\n{'='*55}")
    print(f"Résumé fix_partenaires :")
    print(f"   Noms uniques traites  : {total:,}")
    print(f"   Resolus               : {len(resolved):,}")
    print(f"   Introuvables          : {not_found:,}")
    print(f"   Erreurs API           : {errors:,}")
    print(f"   Lignes mises a jour   : {rows_updated:,}")
    print(f"   Nouveaux en queue     : {added_queue:,}")
    if args.dry_run:
        print("   Mode dry-run -- aucune modification effectuee")


if __name__ == '__main__':
    asyncio.run(main())
