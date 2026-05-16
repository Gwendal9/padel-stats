"""
Test direct de l'API joueurs-padel avec requests (pas de navigateur)
"""
import json, os, requests

COOKIES_FILE = os.path.join(os.path.dirname(__file__), 'cookies.json')

with open(COOKIES_FILE) as f:
    raw = json.load(f)

session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Content-Type': 'application/json',
    'Referer': 'https://tenup.fft.fr/recherche/joueurs/padel',
    'Origin': 'https://tenup.fft.fr',
})
for name, value in raw.items():
    if value and not value.startswith('COLLE_'):
        session.cookies.set(name, value, domain='tenup.fft.fr')

print("🔍 Recherche TAMISIER Mathis...")
r = session.post(
    'https://tenup.fft.fr/back/v1/personnes/joueurs-padel',
    json={"from": 0, "size": 20, "nom": "TAMISIER", "prenom": "Mathis"},
    timeout=15
)
print(f"Status: {r.status_code}")
print(f"Content-Type: {r.headers.get('content-type','')}")
print(f"Réponse: {r.text[:1000]}")
print()

# Test avec juste le nom
print("🔍 Recherche LECARDINAL (juste nom)...")
r2 = session.post(
    'https://tenup.fft.fr/back/v1/personnes/joueurs-padel',
    json={"from": 0, "size": 10, "nom": "LECARDINAL", "prenom": ""},
    timeout=15
)
print(f"Status: {r2.status_code}")
print(f"Réponse: {r2.text[:500]}")
