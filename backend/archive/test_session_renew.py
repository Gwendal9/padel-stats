"""
test_session_renew.py — Le scraper peut-il renouveler SHARED_SESSION_JAVA tout seul ?

Idée : SSESS (login) est stable et longue durée. Si taper une page tenup régénère
un SHARED_SESSION_JAVA valide pour l'API, on automatise le renouvellement → fini
les copier-coller de cookie.

Lance : python test_session_renew.py
"""
import os, sys, json
from curl_cffi import requests as cffi

BASE = os.path.dirname(os.path.abspath(__file__))
TENUP = 'https://tenup.fft.fr'
PID = '1953828852'
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')

hdr = open(os.path.join(BASE, 'cookie_header.txt'), encoding='utf-8').read().strip()
s = cffi.Session(impersonate="chrome124")
# seed le jar depuis cookie_header.txt
for part in hdr.split('; '):
    if '=' in part:
        k, _, v = part.partition('=')
        try: s.cookies.set(k.strip(), v, domain='.fft.fr')
        except Exception: pass

def java():  # valeur courante de SHARED_SESSION_JAVA dans le jar
    try: return s.cookies.get('SHARED_SESSION_JAVA')
    except Exception: return None

H = {'accept': '*/*', 'accept-language': 'fr-FR,fr;q=0.9', 'user-agent': UA,
     'referer': f'{TENUP}/fichejoueur/{PID}'}

print("SHARED_SESSION_JAVA initial :", java())

# 1) bilan avec la session actuelle
r1 = s.get(f"{TENUP}/back/v1/personnes/{PID}/bilan", params={"discipline": "PADEL"}, headers=H, timeout=25)
print(f"\n[1] bilan AVANT renouvellement → HTTP {r1.status_code}")

# 2) on tape des pages tenup pour (peut-être) régénérer SHARED_SESSION_JAVA
for url in [f"{TENUP}/", f"{TENUP}/fichejoueur/{PID}", f"{TENUP}/classement-padel"]:
    rp = s.get(url, headers={'accept': 'text/html,*/*', 'user-agent': UA}, timeout=25)
    print(f"[2] GET {url} → {rp.status_code}, SHARED_SESSION_JAVA={java()}")

# 3) re-bilan avec le jar mis à jour
r3 = s.get(f"{TENUP}/back/v1/personnes/{PID}/bilan", params={"discipline": "PADEL"}, headers=H, timeout=25)
print(f"\n[3] bilan APRÈS → HTTP {r3.status_code}")

print("\n=== VERDICT ===")
if r3.status_code == 200:
    print("🎉 OUI — taper une page régénère une session valide. On peut TOUT automatiser.")
elif r1.status_code == 200:
    print("La session était encore valide (test peu concluant — relance quand c'est en 401).")
else:
    print("❌ Non — la page ne régénère pas de session authentifiée. Il faudra une autre piste.")
