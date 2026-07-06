"""
test_chrome_cookies.py — Peut-on lire les cookies tenup directement depuis Chrome ?

Si oui, le scraper se renouvelle tout seul (plus de copier-coller).
Chrome doit être installé et connecté à tenup.fft.fr.

Installe d'abord :  pip install browser_cookie3 --break-system-packages
Puis :              python test_chrome_cookies.py
"""
import sys

try:
    import browser_cookie3 as bc3
except ImportError:
    print("❌ pip install browser_cookie3 --break-system-packages")
    sys.exit(1)

CLES = ('SHARED_SESSION_JAVA', 'datadome')
SSESS_PREFIX = 'SSESS'

def essaye(nom, fn):
    print(f"\n── {nom} ──")
    try:
        cj = fn(domain_name='fft.fr')
        cookies = list(cj)
        print(f"  ✅ {len(cookies)} cookies fft.fr lus")
        found = {}
        for c in cookies:
            if c.name in CLES or c.name.startswith(SSESS_PREFIX):
                found[c.name] = c.value
        if not found:
            print("  ⚠️  aucun cookie clé trouvé (pas connecté ? mauvais profil ?)")
        for k, v in found.items():
            print(f"     {k} = {str(v)[:24]}…")
        has_auth = any(k.startswith(SSESS_PREFIX) for k in found) and 'SHARED_SESSION_JAVA' in found
        print(f"  → cookies d'auth présents : {has_auth}")
        return has_auth
    except Exception as e:
        print(f"  ❌ échec : {type(e).__name__}: {str(e)[:160]}")
        return False

ok = essaye("Chrome (profil par défaut)", bc3.chrome)
# au cas où l'utilisateur est sur Edge/Firefox
for nom, fn in [("Edge", getattr(bc3, 'edge', None)), ("Firefox", getattr(bc3, 'firefox', None))]:
    if fn and not ok:
        ok = essaye(nom, fn) or ok

print("\n=== VERDICT ===")
print("🎉 Lisible — on peut automatiser la lecture des cookies." if ok else
      "❌ Pas lisible (chiffrement Chrome récent ou profil différent). On passera par une autre piste.")
