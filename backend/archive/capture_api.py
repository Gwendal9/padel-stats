"""
capture_api.py — Capture le token + les vraies URLs API de Tenup.

Ouvre une fiche joueur dans Chromium (avec tes cookies), intercepte tous les
appels /back/ et affiche :
  - le header Authorization (le fameux token qui débloque l'API JSON)
  - chaque URL API appelée + son status + un aperçu de la réponse

But : identifier quelle API renvoie le PROFIL, le PARCOURS (tournois) et les
PARTENAIRES de jeu, et comment s'authentifier.

Usage :
    python capture_api.py                 # fiche par défaut, fenêtre visible
    python capture_api.py --id 7633273415
    python capture_api.py --headless      # tente sans fenêtre

Quand c'est fini : tout est résumé dans la console ET sauvé dans api_capture.json.
Copie-colle la console à Claude (le token sera tronqué pour la sécurité).
"""
import asyncio, json, os, sys, argparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COOKIES_FILE = os.path.join(BASE_DIR, 'cookies.json')
OUT_FILE = os.path.join(BASE_DIR, 'api_capture.json')
TENUP = 'https://tenup.fft.fr'

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')


def load_cookies_pw():
    raw = json.load(open(COOKIES_FILE, encoding='utf-8'))
    out = []
    items = raw if isinstance(raw, list) else [{'name': k, 'value': v} for k, v in raw.items()]
    for c in items:
        pw = {'name': c['name'], 'value': c.get('value', ''),
              'domain': c.get('domain', '.fft.fr'), 'path': c.get('path', '/'),
              'secure': c.get('secure', True), 'httpOnly': c.get('httpOnly', False)}
        if c.get('expirationDate'):
            pw['expires'] = int(c['expirationDate'])
        out.append(pw)
    return out


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--id', default='7633273415')
    ap.add_argument('--headless', action='store_true')
    a = ap.parse_args()

    from playwright.async_api import async_playwright
    import shutil

    captured = []   # appels /back/
    tokens = set()  # valeurs Authorization vues

    async with async_playwright() as p:
        sys_chromium = (shutil.which('chromium-browser') or shutil.which('chromium')
                        or shutil.which('google-chrome'))
        kw = dict(headless=a.headless,
                  args=['--no-sandbox', '--disable-blink-features=AutomationControlled',
                        '--disable-dev-shm-usage'])
        if sys_chromium:
            kw['executable_path'] = sys_chromium
        browser = await p.chromium.launch(**kw)
        ctx = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                       '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            locale='fr-FR')
        await ctx.add_cookies(load_cookies_pw())
        page = await ctx.new_page()

        def on_request(req):
            if '/back/' in req.url:
                h = req.headers
                auth = h.get('authorization') or h.get('x-auth-token') or ''
                if auth:
                    tokens.add(auth)
                captured.append({'method': req.method, 'url': req.url,
                                 'authorization': auth, 'status': None, 'preview': ''})
        page.on('request', on_request)

        async def on_response(resp):
            if '/back/' in resp.url:
                for c in captured:
                    if c['url'] == resp.url and c['status'] is None:
                        c['status'] = resp.status
                        try:
                            t = await resp.text()
                            c['preview'] = t[:300]
                        except Exception:
                            pass
                        break
        page.on('response', lambda r: asyncio.create_task(on_response(r)))

        for url in [f'{TENUP}/fichejoueur/{a.id}', f'{TENUP}/classement/{a.id}/padel']:
            print(f"\n📄 Navigation : {url}")
            try:
                await page.goto(url, wait_until='networkidle', timeout=45000)
            except Exception as e:
                print(f"   (timeout/erreur navigation : {e})")
            await asyncio.sleep(4)

        await ctx.close()
        await browser.close()

    # ── Rapport ──────────────────────────────────────────────
    print(f"\n{'='*70}\n{len(captured)} appels /back/ capturés\n{'='*70}")
    seen = set()
    for c in captured:
        key = (c['method'], c['url'].split('?')[0])
        if key in seen:
            continue
        seen.add(key)
        has_auth = '🔑' if c['authorization'] else '  '
        print(f"\n{has_auth} {c['method']} {c['url']}")
        print(f"     status={c['status']}  auth={'OUI' if c['authorization'] else 'non'}")
        if c['preview']:
            print(f"     réponse: {c['preview'][:200]}")

    print(f"\n{'='*70}")
    if tokens:
        tok = list(tokens)[0]
        print(f"🔑 TOKEN trouvé (type) : {tok.split(' ')[0] if ' ' in tok else 'brut'}")
        print(f"   longueur : {len(tok)} caractères")
        print(f"   début : {tok[:40]}...  (valeur complète sauvée dans api_capture.json)")
    else:
        print("⚠️  Aucun header Authorization vu — l'API utilise peut-être un cookie/token différent.")

    json.dump({'captured': captured, 'tokens': list(tokens)},
              open(OUT_FILE, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print(f"\n💾 Détail complet : {OUT_FILE}")
    print("→ Colle la console à Claude. Ne partage PAS le token complet publiquement.")


if __name__ == '__main__':
    asyncio.run(main())
