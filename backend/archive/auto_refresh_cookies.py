"""
auto_refresh_cookies.py — Rafraîchit les cookies tenup.fft.fr via Playwright
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Ouvre une VRAIE fenêtre Chrome (non headless par défaut), navigue sur
tenup.fft.fr, laisse Queue-IT valider, extrait les cookies et les sauvegarde.

Installation (une seule fois) :
    pip install playwright
    playwright install chromium

Usage :
    python auto_refresh_cookies.py               # fenêtre visible (recommandé)
    python auto_refresh_cookies.py --headless    # sans fenêtre (peut échouer Queue-IT)
    python auto_refresh_cookies.py --timeout 120
"""

import asyncio
import json
import os
import sys
import argparse
from datetime import datetime

# Force UTF-8 sur la console Windows (évite UnicodeEncodeError avec les emojis)
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

COOKIES_FILE = os.path.join(os.path.dirname(__file__), 'cookies.json')
SEED_URL     = 'https://tenup.fft.fr/classement/7633273415/padel'
TENUP_BASE   = 'https://tenup.fft.fr'
QUEUEIT_KEY  = 'QueueITAccepted-SDFrts345E-V3_tenupprod'


async def refresh(headless: bool = False, timeout: int = 90) -> dict:
    try:
        from playwright.async_api import async_playwright, TimeoutError as PWTimeout
    except ImportError:
        print("❌ Playwright non installé.")
        print("   pip install playwright && playwright install chromium")
        sys.exit(1)

    mode = 'headless' if headless else 'fenetre visible'
    print(f"[Playwright] Ouverture Chromium ({mode})...")
    print(f"   URL : {SEED_URL}")

    async with async_playwright() as p:
        # Sur Ubuntu 26.04+, Playwright ne sait pas télécharger Chromium.
        # On utilise le Chromium système si dispo, sinon Playwright géré.
        import shutil
        system_chromium = (
            shutil.which('chromium-browser') or
            shutil.which('chromium') or
            shutil.which('google-chrome')
        )
        launch_kwargs = dict(
            headless=headless,
            args=[
                '--no-sandbox',
                '--disable-blink-features=AutomationControlled',
                '--disable-dev-shm-usage',
            ]
        )
        if system_chromium:
            print(f"   🌐 Chromium système : {system_chromium}")
            launch_kwargs['executable_path'] = system_chromium

        browser = await p.chromium.launch(**launch_kwargs)
        context = await browser.new_context(
            user_agent=(
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0.0.0 Safari/537.36'
            ),
            locale='fr-FR',
            viewport={'width': 1280, 'height': 800},
        )

        # Injecter les cookies actuels pour aider DataDome à reconnaître le navigateur
        if os.path.exists(COOKIES_FILE):
            try:
                with open(COOKIES_FILE) as f:
                    existing = json.load(f)

                # Format liste [{name, value, domain, ...}]
                if isinstance(existing, list):
                    pw_cookies = []
                    for c in existing:
                        name  = c.get('name', '')
                        value = c.get('value', '')
                        if not (name and value):
                            continue
                        pw_cookies.append({
                            'name': name,
                            'value': value,
                            'domain': c.get('domain', 'tenup.fft.fr').lstrip('.'),
                            'path': c.get('path', '/'),
                            'secure': c.get('secure', False),
                            'httpOnly': c.get('httpOnly', False),
                            'sameSite': 'Lax',
                        })
                    if pw_cookies:
                        await context.add_cookies(pw_cookies)
                        print(f"   🍪 {len(pw_cookies)} cookies existants injectés")
            except Exception as e:
                print(f"   ⚠️  Cookies existants non chargés : {e}")

        page = await context.new_page()

        # Masquer les traces d'automation
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            window.chrome = { runtime: {} };
        """)

        if not headless:
            print("   👁️  Une fenêtre Chrome va s'ouvrir — ne la ferme pas !")
            print("   ⏳ Attente de Queue-IT...")

        # Navigation principale
        try:
            await page.goto(SEED_URL, wait_until='domcontentloaded', timeout=timeout * 1000)
        except Exception as e:
            print(f"   ⚠️  Navigation : {e}")

        # Attendre que l'URL revienne sur tenup.fft.fr (Queue-IT terminé)
        deadline = asyncio.get_event_loop().time() + timeout
        last_print = 0

        while asyncio.get_event_loop().time() < deadline:
            url = page.url
            now = asyncio.get_event_loop().time()

            if 'tenup.fft.fr' in url and 'queue-it' not in url.lower():
                print(f"   ✅ Sur tenup.fft.fr — Queue-IT passé !")
                break

            if now - last_print > 5:
                if 'queue-it.net' in url.lower():
                    print(f"   🔄 En attente Queue-IT... ({url[:60]})")
                last_print = now

            await asyncio.sleep(1)
        else:
            print(f"   ⚠️  Timeout {timeout}s — URL : {page.url[:80]}")

        # Attente supplémentaire pour que les cookies soient posés
        await asyncio.sleep(3)

        # Navigation vers /recherche pour déclencher la session Java (SHARED_SESSION_JAVA)
        print("   🔄 Navigation /recherche pour session Java...")
        try:
            await page.goto(f'{TENUP_BASE}/recherche/joueurs/padel',
                            wait_until='domcontentloaded', timeout=30000)
            await asyncio.sleep(3)
            print("   ✅ Session Java établie")
        except Exception as e:
            print(f"   ⚠️  Navigation recherche : {e}")

        # Extraction des cookies
        all_cookies = await context.cookies(['https://tenup.fft.fr'])
        await browser.close()

        if not all_cookies:
            print("   ❌ Aucun cookie récupéré !")
            return {}

        # Convertir en format liste (compatible avec cookies.json existant)
        cookie_list = []
        for c in all_cookies:
            cookie_list.append({
                'name':       c['name'],
                'value':      c['value'],
                'domain':     c.get('domain', 'tenup.fft.fr'),
                'path':       c.get('path', '/'),
                'secure':     c.get('secure', False),
                'httpOnly':   c.get('httpOnly', False),
                'expirationDate': c.get('expires', 0),
            })

        # Vérifications
        names = {c['name'] for c in cookie_list}
        if QUEUEIT_KEY not in names:
            print(f"   ❌ QueueITAccepted manquant — Queue-IT pas encore validé !")
            print(f"   → Cookies présents : {', '.join(sorted(names))[:200]}")
            return {}

        queueit_val = next(c['value'] for c in cookie_list if c['name'] == QUEUEIT_KEY)
        print(f"   🔑 QueueITAccepted : {queueit_val[:60]}...")

        # Préserver SHARED_SESSION_JAVA depuis l'ancien cookies.json s'il est absent
        # (ce cookie ne s'obtient que via une session authentifiée utilisateur)
        PRESERVE_KEYS = {'SHARED_SESSION_JAVA'}
        new_names = {c['name'] for c in cookie_list}
        missing = PRESERVE_KEYS - new_names
        if missing and os.path.exists(COOKIES_FILE):
            try:
                with open(COOKIES_FILE) as f:
                    old = json.load(f)
                if isinstance(old, list):
                    for old_c in old:
                        if old_c.get('name') in missing and old_c.get('value'):
                            cookie_list.append(old_c)
                            print(f"   🔒 Préservé depuis l'ancien fichier : {old_c['name']}")
            except Exception:
                pass

        print(f"   ✅ {len(cookie_list)} cookies récupérés")
        return cookie_list


def save_cookies(cookie_list: list):
    if not cookie_list:
        return

    # Backup
    if os.path.exists(COOKIES_FILE):
        try:
            with open(COOKIES_FILE) as f:
                old = f.read()
            with open(COOKIES_FILE + '.bak', 'w') as f:
                f.write(old)
        except Exception:
            pass

    with open(COOKIES_FILE, 'w') as f:
        json.dump(cookie_list, f, indent=2)

    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"   💾 cookies.json sauvegardé ({ts}) — {len(cookie_list)} cookies")


async def main():
    parser = argparse.ArgumentParser(description='Auto-refresh cookies tenup.fft.fr')
    parser.add_argument('--headless', action='store_true',
                        help='Mode sans fenêtre (peut échouer Queue-IT)')
    parser.add_argument('--visible',  action='store_true',
                        help='Forcer fenêtre visible (défaut)')
    parser.add_argument('--timeout', type=int, default=90,
                        help='Timeout en secondes (défaut: 90)')
    args = parser.parse_args()

    headless = args.headless and not args.visible
    cookie_list = await refresh(headless=headless, timeout=args.timeout)

    if cookie_list:
        save_cookies(cookie_list)
        print("\n✅ Cookies rafraîchis avec succès !")
        return 0
    else:
        print("\n❌ Échec du refresh")
        return 1


if __name__ == '__main__':
    code = asyncio.run(main())
    sys.exit(code)
