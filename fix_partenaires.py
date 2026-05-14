"""
fix_partenaires.py
──────────────────
Rattrapage des partenaires manquants dans la DB.

Problème : pendant les runs où l'API renvoyait 401, les participations ont été
enregistrées avec partenaire_nom mais partenaire_id = NULL (ou vide).
Ces joueurs n'ont donc jamais été ajoutés à la scrape_queue.

Ce script :
1. Lance un browser Playwright (qui gère l'auth via cookies de session)
2. Lit toutes les participations avec partenaire_nom non vide et partenaire_id vide
3. Résout les IDs via context.request.post (authentifié automatiquement)
4. Met à jour participations.partenaire_id
5. Ajoute les joueurs inconnus à la scrape_queue

Usage :
    python fix_partenaires.py
    python fix_partenaires.py --dry-run   # affiche sans modifier la DB
    python fix_partenaires.py --limit 500 # traite 500 partenaires max
    python fix_partenaires.py --visible   # browser visible pour debug
"""

import asyncio
import argparse
import json
import os
import sqlite3
import time

from playwright.async_api import async_playwright

# ── Config ────────────────────────────────────────────────────────
BASE_DIR       = os.path.dirname(__file__)
DB_FILE        = os.path.join(BASE_DIR, 'tenup.db')
COOKIES_FILE   = os.path.join(BASE_DIR, 'cookies.json')
TENUP_BASE     = 'https://tenup.fft.fr'
API_SEARCH_URL = f'{TENUP_BASE}/back/v1/personnes/joueurs-padel'

# ── Chargement cookies ────────────────────────────────────────────
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

# ── Parsing nom partenaire ────────────────────────────────────────
def parse_partner_name(full_name):
    if not full_name:
        return None, None
    parts = full_name.strip().split()
    if len(parts) < 2:
        return None, None
    prenom_parts = parts[-1:]
    nom_parts    = parts[:-1]
    return ' '.join(nom_parts), ' '.join(prenom_parts)

# ── Résolution via Playwright context.request ─────────────────────
async def _api_post(context, nom, prenom):
    """Appel brut à l'API, retourne la liste de joueurs ou None."""
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
        print(f"   ⚠️  Erreur API: {e}")
    return None, None

async def resolve_partner(context, partenaire_nom):
    """Retourne l'id_fft trouvé, None si pas trouvé, '401' si auth morte.
    Essaie les deux ordres : 'NOM Prénom' et 'Prénom NOM'."""
    parts = partenaire_nom.strip().split()
    if len(parts) < 2:
        return None

    # Génère les deux combinaisons possibles (premier mot = prénom OU dernier mot = prénom)
    candidats = [
        (parts[-1], ' '.join(parts[:-1])),   # dernier = prénom, reste = nom
        (parts[0],  ' '.join(parts[1:])),     # premier = prénom, reste = nom
    ]
    # Dédupliquer si les deux sont identiques (2 mots seulement)
    if candidats[0] == candidats[1]:
        candidats = [candidats[0]]

    for prenom, nom in candidats:
        if not prenom or not nom:
            continue
        joueurs, err = await _api_post(context, nom, prenom)
        if err == '401':
            return '401'
        if joueurs:
            return str(joueurs[0]['idCrm'])
        await asyncio.sleep(0.1)

    return None

# ── DB helpers ────────────────────────────────────────────────────
def get_missing_partners(conn, limit=0):
    q = """
        SELECT rowid, partenaire_nom
        FROM participations
        WHERE (partenaire_id IS NULL OR partenaire_id = '')
          AND partenaire_nom IS NOT NULL
          AND partenaire_nom != ''
        ORDER BY rowid
    """
    if limit > 0:
        q += f" LIMIT {limit}"
    return conn.execute(q).fetchall()

def is_known(conn, id_fft):
    in_queue   = conn.execute("SELECT 1 FROM scrape_queue WHERE id_fft=?", (id_fft,)).fetchone()
    in_joueurs = conn.execute("SELECT 1 FROM joueurs WHERE id_fft=?", (id_fft,)).fetchone()
    return bool(in_queue or in_joueurs)

def add_to_queue(conn, id_fft):
    conn.execute(
        "INSERT OR IGNORE INTO scrape_queue (id_fft, statut, added_at) VALUES (?, 'pending', datetime('now'))",
        (id_fft,)
    )

# ── Main ──────────────────────────────────────────────────────────
async def main():
    parser = argparse.ArgumentParser(description='Rattrapage partenaires manquants')
    parser.add_argument('--dry-run', action='store_true', help='Affiche sans modifier la DB')
    parser.add_argument('--limit',   type=int, default=0,  help='Nb max de partenaires à traiter (0=tous)')
    parser.add_argument('--visible', action='store_true',  help='Browser visible')
    args = parser.parse_args()

    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA busy_timeout=15000")

    total_missing = conn.execute(
        "SELECT COUNT(*) FROM participations WHERE (partenaire_id IS NULL OR partenaire_id='') AND partenaire_nom IS NOT NULL AND partenaire_nom != ''"
    ).fetchone()[0]
    print(f"📊 Participations sans partenaire_id : {total_missing:,}")

    if total_missing == 0:
        print("✅ Rien à faire !")
        conn.close()
        return

    rows = get_missing_partners(conn, limit=args.limit)
    print(f"🔍 {len(rows):,} partenaires à résoudre{' (limité)' if args.limit else ''}...")
    if args.dry_run:
        print("⚠️  Mode dry-run — aucune modification ne sera effectuée\n")

    cookies = load_cookies()

    resolved      = 0
    added_to_queue = 0
    not_found     = 0
    errors        = 0

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

        # Charger une vraie page d'abord pour établir la session (contourne Queue-IT)
        print("🌐 Chargement page pour établir la session...")
        page = await context.new_page()
        try:
            await page.goto(f'{TENUP_BASE}/recherche/joueurs/padel',
                            wait_until='domcontentloaded', timeout=60000)
            await asyncio.sleep(3)
            print(f"   URL finale : {page.url}")
        except Exception as e:
            print(f"   ⚠️  Navigation: {e}")
        finally:
            await page.close()

        # Test rapide de l'auth avant de commencer
        print("🔐 Vérification auth API...")
        try:
            resp = await context.request.post(
                API_SEARCH_URL,
                data=json.dumps({"from": 0, "size": 3, "nom": "ROLLAND", "prenom": "Gwendal"}),
                headers={
                    'Content-Type': 'application/json',
                    'Accept': 'application/json, text/plain, */*',
                    'Referer': f'{TENUP_BASE}/recherche/joueurs/padel',
                    'Origin': TENUP_BASE,
                }
            )
            print(f"   Status : {resp.status}")
            body = await resp.text()
            print(f"   Body   : {body[:200]}")
            if resp.status != 200:
                print("❌ Auth invalide — rafraîchir les cookies et relancer.")
                await browser.close()
                conn.close()
                return
            print(f"✅ Auth OK\n")
        except Exception as e:
            print(f"❌ Erreur test auth: {e}")
            await browser.close()
            conn.close()
            return

        for i, (rowid, partenaire_nom) in enumerate(rows):
            pid = await resolve_partner(context, partenaire_nom)

            if pid == '401':
                print(f"\n⛔ API 401 à la ligne {i+1} — rafraîchir les cookies et relancer.")
                errors += 1
                break
            elif pid:
                if not args.dry_run:
                    conn.execute(
                        "UPDATE participations SET partenaire_id=? WHERE rowid=?",
                        (pid, rowid)
                    )
                    if not is_known(conn, pid):
                        add_to_queue(conn, pid)
                        added_to_queue += 1
                resolved += 1
            else:
                not_found += 1

            # Commit + log tous les 100
            if (i + 1) % 100 == 0:
                if not args.dry_run:
                    conn.commit()
                pct = (i + 1) / len(rows) * 100
                print(f"   [{i+1}/{len(rows)}] {pct:.1f}% — ✅ {resolved} résolus | ➕ {added_to_queue} en queue | ❓ {not_found} introuvables")

            await asyncio.sleep(0.15)  # ~6 req/s, bien en dessous des limites

        await context.close()
        await browser.close()

    if not args.dry_run:
        conn.commit()
    conn.close()

    print(f"\n{'='*55}")
    print(f"📊 Résumé fix_partenaires :")
    print(f"   Partenaires résolus   : {resolved:,}")
    print(f"   Nouveaux en queue     : {added_to_queue:,}")
    print(f"   Introuvables          : {not_found:,}")
    print(f"   Erreurs API           : {errors:,}")
    if args.dry_run:
        print(f"   ⚠️  Mode dry-run — aucune modification effectuée")


if __name__ == '__main__':
    asyncio.run(main())
