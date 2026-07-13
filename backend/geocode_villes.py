"""
geocode_villes.py — Coordonnées lat/lon des villes (pour marqueurs de carte).

Source : API officielle gratuite geo.api.gouv.fr (toutes les communes FR + coords, sans clé).
Le script télécharge une fois (cache local communes_geo.json), puis matche les villes de
`joueurs`/`clubs` par NOM + DÉPARTEMENT (fiable contre les homonymes ; il existe plein de
communes homonymes selon le département).

Produit :
  - table `villes_geo` (ville, dept_num, lat, lon)
  - colonnes lat/lon ajoutées à `clubs` (pour poser les marqueurs)
À lancer APRÈS build_geo.py (qui remplit dept_num). Nécessite Internet (machine Windows).

Usage :
    python geocode_villes.py            # télécharge (ou cache) + matche + écrit
    python geocode_villes.py --refresh  # force le re-téléchargement des communes
"""
import sqlite3, sys, os, json, unicodedata, urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, 'tenup.db')
CACHE = os.path.join(BASE, 'communes_geo.json')
API = "https://geo.api.gouv.fr/communes?fields=nom,centre,codeDepartement&format=json"
REFRESH = '--refresh' in sys.argv


def norm(s):
    if not s: return ''
    s = unicodedata.normalize('NFKD', s)
    s = ''.join(c for c in s if not unicodedata.combining(c)).upper()
    for ch in "-'.": s = s.replace(ch, ' ')
    s = ' '.join(s.split())
    # arrondissements des grandes villes
    for v in ('PARIS', 'LYON', 'MARSEILLE'):
        if s.startswith(v + ' ') and s[len(v)+1:].strip().isdigit():
            return v
    # abréviations courantes
    s = ' '.join(('SAINT' if w == 'ST' else 'SAINTE' if w == 'STE' else w) for w in s.split())
    return s


def load_communes():
    if os.path.exists(CACHE) and not REFRESH:
        print(f"Communes : cache {CACHE}")
        return json.load(open(CACHE, encoding='utf-8'))
    print(f"Téléchargement des communes depuis geo.api.gouv.fr…")
    req = urllib.request.Request(API, headers={'User-Agent': 'tenup-geocode'})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.load(r)
    json.dump(data, open(CACHE, 'w', encoding='utf-8'))
    print(f"  {len(data):,} communes téléchargées et mises en cache.")
    return data


def main():
    communes = load_communes()
    # index (nom_normalisé, dept) -> (lat,lon)  + index nom seul si unique
    by_nd, by_name = {}, {}
    seen_name = {}
    for com in communes:
        centre = com.get('centre') or {}
        coords = centre.get('coordinates')
        if not coords:
            continue
        lon, lat = coords[0], coords[1]
        nm = norm(com.get('nom'))
        dep = com.get('codeDepartement')
        by_nd[(nm, dep)] = (lat, lon)
        seen_name.setdefault(nm, set()).add((lat, lon, dep))
    for nm, pts in seen_name.items():
        if len(pts) == 1:
            by_name[nm] = next(iter(pts))   # (lat, lon, dep)
    print(f"index : {len(by_nd):,} (nom+dept), {len(by_name):,} noms uniques")

    c = sqlite3.connect(DB, isolation_level=None)
    c.execute("PRAGMA busy_timeout=60000")  # attend si la base est verrouillée (autre script en cours)
    c.execute("""CREATE TABLE IF NOT EXISTS villes_geo (
        ville TEXT, dept_num TEXT, lat REAL, lon REAL, UNIQUE(ville, dept_num))""")

    couples = c.execute("""SELECT DISTINCT ville, dept_num FROM joueurs
                           WHERE ville IS NOT NULL AND ville!=''""").fetchall()
    ok = miss = crossdept = 0
    rows = []
    ex_miss = []
    for ville, dep in couples:
        nm = norm(ville)
        pt = by_nd.get((nm, dep))
        if not pt:
            cand = by_name.get(nm)  # (lat, lon, dep_commune) ou None
            # Fallback "nom seul" UNIQUEMENT si meme departement (ou dept club inconnu).
            # Sinon on refuse : evite qu'un homonyme d'un autre dept place le club au
            # mauvais endroit (ex. LUYNES 13 -> Luynes 37, ou une ville mal saisie).
            if cand and (not dep or cand[2] == dep):
                pt = (cand[0], cand[1])
            elif cand:
                crossdept += 1
        if pt:
            rows.append((ville, dep, pt[0], pt[1])); ok += 1
        else:
            miss += 1
            if len(ex_miss) < 8: ex_miss.append(f"{ville} ({dep})")

    c.execute("BEGIN")
    c.execute("DELETE FROM villes_geo")
    c.executemany("INSERT OR IGNORE INTO villes_geo (ville,dept_num,lat,lon) VALUES (?,?,?,?)", rows)
    c.execute("COMMIT")
    tot = len(couples)
    print(f"\nVilles géocodées : {ok:,}/{tot:,} ({100*ok/max(tot,1):.1f}%)  | non trouvées : {miss:,}")
    if crossdept: print(f"  (dont {crossdept:,} refusées : homonyme dans un autre département — évite les clubs mal placés)")
    if ex_miss: print(f"  ex. non trouvées : {ex_miss}")

    # lat/lon sur les clubs (via ville + dept)
    for mig in ["ALTER TABLE clubs ADD COLUMN lat REAL", "ALTER TABLE clubs ADD COLUMN lon REAL"]:
        try: c.execute(mig)
        except Exception: pass
    c.execute("""UPDATE clubs SET
        lat=(SELECT v.lat FROM villes_geo v WHERE v.ville=clubs.ville AND v.dept_num=clubs.dept_num),
        lon=(SELECT v.lon FROM villes_geo v WHERE v.ville=clubs.ville AND v.dept_num=clubs.dept_num)""")
    nclub = c.execute("SELECT COUNT(*) FROM clubs WHERE lat IS NOT NULL").fetchone()[0]
    print(f"Clubs avec coordonnées : {nclub:,}")

    print("\n→ Pour la carte : marqueurs via clubs(lat,lon) ou villes_geo ; "
          "choroplèthe départements via joueurs.dept_num + GeoJSON FR.")


if __name__ == '__main__':
    main()
