"""
dump_bilan.py — Sauvegarde le JSON complet du bilan FFT d'un joueur dans un fichier,
pour inspection (but : trouver le champ "date du tournoi" que scraper_json.py ne
branche pas actuellement dans participations.date_tournoi).

Usage :
    python dump_bilan.py --id 7633273415
"""
import os, sys, json, argparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COOKIE_FILE = os.path.join(BASE_DIR, 'cookie_header.txt')
TENUP = 'https://tenup.fft.fr'

try:
    from curl_cffi import requests as cffi
except ImportError:
    print("❌ pip install curl_cffi --break-system-packages")
    sys.exit(1)

ap = argparse.ArgumentParser()
ap.add_argument('--id', required=True)
a = ap.parse_args()

if not os.path.exists(COOKIE_FILE):
    print(f"❌ {COOKIE_FILE} introuvable — colle ton cookie dedans (voir cookie_header.txt).")
    sys.exit(1)

cookie = open(COOKIE_FILE, encoding='utf-8').read().strip()
headers = {
    'accept': '*/*', 'accept-language': 'fr-FR,fr;q=0.9', 'cookie': cookie, 'referer': TENUP,
    'user-agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                    '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'),
}

sess = cffi.Session(impersonate="chrome124")
url = f"{TENUP}/back/v1/personnes/{a.id}/bilan?discipline=PADEL"
resp = sess.get(url, headers=headers)
print("HTTP", resp.status_code, "|", len(resp.content), "octets")

if resp.status_code != 200:
    print(resp.text[:500])
    sys.exit(1)

data = resp.json()
out_path = os.path.join(BASE_DIR, f"bilan_{a.id}.json")
with open(out_path, 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f"\n💾 Sauvegardé dans {out_path}")
comps = data.get('competitions') or []
print(f"Nombre de competitions : {len(comps)}")
if comps:
    print("Clés d'une compétition (la première) :", list(comps[0].keys()))
    print("\nExemple complet (1re compétition) :")
    print(json.dumps(comps[0], ensure_ascii=False, indent=2))
