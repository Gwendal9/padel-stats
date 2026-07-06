"""
test_api_endpoints.py — Teste l'API JSON Tenup DEPUIS L'EXTÉRIEUR du navigateur.

But : confirmer que curl_cffi (hors navigateur) passe DataDome sur les endpoints
/back/v1 découverts, avec juste les cookies. Si ça marche → scraper JSON viable.

ÉTAPE PRÉALABLE (30s) :
  1. Dans Chrome (ta vraie session), DevTools > Network > clique une requête /back/
  2. Onglet Headers > Request Headers > ligne "cookie:" > clic droit > Copy value
     (ou sélectionne toute la valeur après "cookie:" et copie)
  3. Colle-la dans un fichier  backend/cookie_header.txt  (une seule ligne)
  Ces cookies frais correspondent à ta session en cours (datadome + SSESS + QueueIT).

Usage :
    pip install curl_cffi --break-system-packages
    python test_api_endpoints.py
    python test_api_endpoints.py --idpersonne 1953828852
"""
import os, sys, json, argparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COOKIE_HEADER_FILE = os.path.join(BASE_DIR, 'cookie_header.txt')
TENUP = 'https://tenup.fft.fr'

try:
    from curl_cffi import requests as cffi
    SESS = cffi.Session(impersonate="chrome124")
    print("✅ curl_cffi (fingerprint Chrome)\n")
except ImportError:
    print("❌ Installe curl_cffi : pip install curl_cffi --break-system-packages")
    sys.exit(1)


def load_cookie_header():
    if not os.path.exists(COOKIE_HEADER_FILE):
        print(f"❌ Crée {COOKIE_HEADER_FILE} avec la valeur du header 'cookie:' (voir en-tête du script).")
        sys.exit(1)
    return open(COOKIE_HEADER_FILE, encoding='utf-8').read().strip()


def headers(cookie, referer=TENUP):
    return {
        'accept': '*/*',
        'accept-language': 'fr-FR,fr;q=0.9',
        'cookie': cookie,
        'referer': referer,
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                      '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    }


def show(label, resp):
    print(f"\n{'='*70}\n{label}\n  HTTP {resp.status_code} | {len(resp.content)} octets")
    body = resp.text
    low = body.lower()
    if resp.status_code == 403 or 'datadome' in low or 'captcha' in low:
        print("  🚫 BLOQUÉ (DataDome/403). Voir flags :",
              [w for w in ('datadome', 'captcha', 'blocked') if w in low])
        print("  " + body[:200].replace('\n', ' '))
        return False
    if resp.status_code == 401:
        print("  🔑 401 — cookie/session EXPIRÉ. Réexporte cookie_header.txt.")
        print("  " + body[:200].replace('\n', ' '))
        return False
    if resp.status_code != 200:
        print(f"  ⚠️  HTTP {resp.status_code} :", body[:200].replace('\n', ' '))
        return False
    try:
        j = resp.json()
        cles = f"[array {len(j)}]" if isinstance(j, list) else ', '.join(list(j.keys())[:12])
        print(f"  ✅ JSON OK. Clés : {cles}")
        print("  " + json.dumps(j, ensure_ascii=False)[:500])
        return True
    except Exception:
        print("  ⚠️  Pas du JSON :", body[:200].replace('\n', ' '))
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--idpersonne', default='1953828852')
    a = ap.parse_args()
    pid = a.idpersonne
    ck = load_cookie_header()
    print(f"Cookie chargé ({len(ck)} car). datadome présent : {'datadome=' in ck}")

    ok = []
    ok.append(show("1) POST /back/public/v2/classements/recherche  (liste bulk + idPersonne ?)",
        SESS.post(f"{TENUP}/back/public/v2/classements/recherche",
                  headers={**headers(ck), 'content-type': 'application/json', 'origin': TENUP},
                  data=json.dumps({"from": 0, "size": 3, "discipline": "PADEL", "sexe": "H"}),
                  timeout=20)))

    ok.append(show(f"2) GET /back/v1/personnes/{pid}/bilan?discipline=PADEL  (parcours+classement)",
        SESS.get(f"{TENUP}/back/v1/personnes/{pid}/bilan",
                 params={"discipline": "PADEL"},
                 headers=headers(ck, f"{TENUP}/fichejoueur/{pid}"), timeout=20)))

    ok.append(show(f"3) GET /back/v1/personnes/{pid}/profil-joueur  (identité+club)",
        SESS.get(f"{TENUP}/back/v1/personnes/{pid}/profil-joueur",
                 headers=headers(ck, f"{TENUP}/fichejoueur/{pid}"), timeout=20)))

    print(f"\n{'='*70}\nRÉSULTAT : {sum(ok)}/3 endpoints OK depuis l'extérieur du navigateur.")
    if all(ok):
        print("🎉 curl_cffi passe DataDome → pipeline JSON 100% viable, sans navigateur.")
    elif any(ok):
        print("⚠️  Partiel. Vérifie les cookies (frais ?) ou ajoute un proxy pour les bloqués.")
    else:
        print("🚫 Tout bloqué. Cookies périmés OU DataDome bloque curl → il faudra des proxies.")


if __name__ == '__main__':
    main()
