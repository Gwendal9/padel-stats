"""
enrich_clubs.py
───────────────
Complète automatiquement clubs.json en interrogeant l'API adresse.data.gouv.fr
pour chaque ville non encore mappée dans la base de joueurs.

API utilisée : https://api-adresse.data.gouv.fr  (gratuite, sans clé, gov FR)
Département déduit depuis le code INSEE de la commune.

Usage : python enrich_clubs.py
"""

import sqlite3, json, os, time, sys
import urllib.request, urllib.parse

DB_FILE    = os.path.join(os.path.dirname(__file__), 'tenup.db')
CLUBS_FILE = os.path.join(os.path.dirname(__file__), 'clubs.json')

# ── Charger clubs.json existant ───────────────────────────────────────────────
with open(CLUBS_FILE, encoding='utf-8') as f:
    clubs = json.load(f)
clubs.pop('_note', None)
print(f"📁  clubs.json chargé : {len(clubs)} entrées existantes")

# ── Lire toutes les villes de la base, triées par fréquence ──────────────────
conn = sqlite3.connect(DB_FILE)
conn.execute("PRAGMA journal_mode=WAL")
rows = conn.execute("""
    SELECT ville, COUNT(*) AS c
    FROM joueurs
    WHERE ville IS NOT NULL AND ville != ''
    GROUP BY ville
    ORDER BY c DESC
""").fetchall()
conn.close()

manquantes = [(v, c) for v, c in rows if v not in clubs]
print(f"🔍  {len(rows)} villes en base — {len(manquantes)} non mappées\n")

if not manquantes:
    print("✅  Rien à faire, clubs.json est déjà complet !")
    sys.exit(0)

# ── Extraction du code département depuis le code INSEE ──────────────────────
def insee_to_dept(citycode: str) -> str:
    """
    Convertit un code INSEE commune → code département.
    Exemples :
        75056  → 75  (Paris)
        92012  → 92  (Hauts-de-Seine)
        97209  → 972 (Martinique)
        2A004  → 2A  (Corse-du-Sud)
    """
    if not citycode:
        return ""
    if citycode.startswith("97"):
        return citycode[:3]          # DOM-TOM  : 971 972 973 974 976
    if citycode[:2] in ("2A", "2B"):
        return citycode[:2]          # Corse
    return citycode[:2]              # Métropole : 01 … 95

# ── Requête à l'API gouvernementale ──────────────────────────────────────────
def get_dept_from_api(ville: str) -> tuple[str | None, float]:
    """
    Interroge l'API adresse.data.gouv.fr pour une ville.
    Retourne (code_dept, score_confiance) ou (None, 0) si non trouvé.

    L'API retourne les 3 meilleurs candidats ; on prend celui avec le
    score le plus élevé. Si deux résultats ont le même score, on prend
    le premier (classé par pertinence par l'API).
    """
    try:
        q   = urllib.parse.quote(ville.strip())
        url = (
            f"https://api-adresse.data.gouv.fr/search/"
            f"?q={q}&type=municipality&limit=3&autocomplete=0"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "tenup-padel-enricher/1.0"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read())

        features = data.get("features", [])
        if not features:
            return None, 0.0

        best   = max(features, key=lambda f: f["properties"].get("score", 0))
        props  = best["properties"]
        score  = props.get("score", 0)
        code   = props.get("citycode", "")
        dept   = insee_to_dept(code)
        return dept or None, score

    except Exception as err:
        return None, 0.0

# ── Mapping en batch ──────────────────────────────────────────────────────────
SAVE_EVERY   = 100    # sauvegarde intermédiaire tous les N essais
DELAY        = 0.11   # ~9 req/s (limite officieuse API = 10/s)
MIN_SCORE    = 0.30   # score minimum pour accepter un résultat

added   = 0
skipped = []   # villes non trouvées ou score trop faible

print(f"{'N°':>5}  {'Joueurs':>7}  {'Ville':<35}  {'Dept':>5}  Score")
print("─" * 70)

for i, (ville, count) in enumerate(manquantes, 1):
    dept, score = get_dept_from_api(ville)

    if dept and score >= MIN_SCORE:
        clubs[ville] = {"dept": dept}
        added += 1
        flag = "✅"
    else:
        skipped.append((ville, count, dept, score))
        flag = "❓" if not dept else "⚠️ "  # ⚠️ = trouvé mais score faible

    print(f"{i:>5}  {count:>7}  {ville:<35}  {dept or '—':>5}  {score:.2f}  {flag}")

    # Sauvegarde intermédiaire
    if i % SAVE_EVERY == 0:
        with open(CLUBS_FILE, 'w', encoding='utf-8') as f:
            json.dump(clubs, f, ensure_ascii=False, indent=2)
        print(f"\n  💾  Sauvegarde intermédiaire — {len(clubs)} entrées dans clubs.json\n")

    time.sleep(DELAY)

# ── Sauvegarde finale ─────────────────────────────────────────────────────────
with open(CLUBS_FILE, 'w', encoding='utf-8') as f:
    json.dump(clubs, f, ensure_ascii=False, indent=2)

print("\n" + "═" * 70)
print(f"✅  {added} nouvelles villes ajoutées → clubs.json ({len(clubs)} entrées au total)")

if skipped:
    print(f"\n❓  {len(skipped)} villes non résolues (score trop faible ou inconnues) :")
    print(f"    {'Joueurs':>7}  {'Ville':<35}  {'Dept API':>8}  Score")
    for ville, count, dept, score in sorted(skipped, key=lambda x: -x[1])[:40]:
        print(f"    {count:>7}  {ville:<35}  {dept or '—':>8}  {score:.2f}")
    if len(skipped) > 40:
        print(f"    … et {len(skipped)-40} autres (très peu de joueurs)")
    print(f"\n    → Ces villes s'afficheront en 'Non mappé' dans le graphe.")
    print(f"      Si tu veux les résoudre manuellement, uploads le résultat ici.")

print("\n💡  Lance ensuite : python generate_graph.py")
