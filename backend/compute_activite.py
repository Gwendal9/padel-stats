"""
compute_activite.py — Calcule le statut d'activité de chaque joueur.

Règle (validée par Gwendal, 09/2026) :
  - actif       : a joué au moins un tournoi dans les 3 derniers mois.
  - en_pause    : dernier tournoi entre 3 et 12 mois (zone grise, nom provisoire,
                  à renommer si besoin).
  - en_retraite : a déjà joué au moins un tournoi, mais plus rien depuis 12 mois.
  - jamais_joue : aucune participation datable trouvée (jamais vu dans
                  `participations`, OU vu mais sans aucune date exploitable).

IMPORTANT : ce script ne supprime JAMAIS aucun joueur, quel que soit son statut
("on garde tous ces joueurs au cas où ils reviennent" — Gwendal). Il se contente
de mettre à jour 3 colonnes sur `joueurs` : statut_activite, dernier_tournoi_date,
dernier_tournoi_estime.

LIMITE CONNUE (09/2026) : `date_tournoi` n'est plus alimenté par le scraper JSON
depuis mars 2026 (chantier en cours, voir mémoire projet). En attendant ce fix, ce
script ESTIME la date d'un tournoi récent à partir de `expiration`
(= date_tournoi + ~12 mois côté FFT — vérifié sur 493 lignes ayant les deux dates :
écart médian 363 jours, min 349, max 379). Ces cas sont marqués
`dernier_tournoi_estime=1` (0 si date réelle issue de date_tournoi). Une fois
date_tournoi corrigé, relancer ce script recalculera avec les vraies dates.

Usage :
    python compute_activite.py            # calcule + affiche la distribution (dry-run)
    python compute_activite.py --apply    # + écrit réellement en base
"""
import sqlite3, sys, os
from datetime import date

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tenup.db')
APPLY = '--apply' in sys.argv
TODAY = date.today().isoformat()

conn = sqlite3.connect(DB, isolation_level=None)

for m in [
    "ALTER TABLE joueurs ADD COLUMN statut_activite TEXT",
    "ALTER TABLE joueurs ADD COLUMN dernier_tournoi_date TEXT",
    "ALTER TABLE joueurs ADD COLUMN dernier_tournoi_estime INTEGER",
]:
    try:
        conn.execute(m)
    except Exception:
        pass

print(f"=== compute_activite — {'EXÉCUTION' if APPLY else 'SIMULATION (dry-run)'} | référence={TODAY} ===\n")

conn.executescript("""
DROP TABLE IF EXISTS _eff;
CREATE TEMP TABLE _eff AS
SELECT id_joueur,
       CASE WHEN date_tournoi IS NOT NULL AND length(date_tournoi) >= 10
            THEN substr(date_tournoi,7,4)||'-'||substr(date_tournoi,4,2)||'-'||substr(date_tournoi,1,2)
            ELSE date(substr(expiration,1,10), '-365 days')
       END AS eff_date,
       CASE WHEN date_tournoi IS NOT NULL AND length(date_tournoi) >= 10 THEN 0 ELSE 1 END AS is_estime
FROM participations
WHERE (date_tournoi IS NOT NULL AND length(date_tournoi) >= 10)
   OR (expiration IS NOT NULL AND length(expiration) >= 10);

DROP TABLE IF EXISTS _last;
CREATE TEMP TABLE _last AS
SELECT id_joueur, MAX(eff_date) AS last_date
FROM _eff
WHERE eff_date IS NOT NULL
GROUP BY id_joueur;

DROP TABLE IF EXISTS _last_full;
CREATE TEMP TABLE _last_full AS
SELECT l.id_joueur, l.last_date, MIN(e.is_estime) AS is_estime
FROM _last l JOIN _eff e ON e.id_joueur = l.id_joueur AND e.eff_date = l.last_date
GROUP BY l.id_joueur;
""")

rows = conn.execute("SELECT id_joueur, last_date, is_estime FROM _last_full").fetchall()
print(f"Joueurs avec au moins une participation datable : {len(rows):,}")

seuil_actif = conn.execute("SELECT date(?, '-3 months')", (TODAY,)).fetchone()[0]
seuil_retraite = conn.execute("SELECT date(?, '-12 months')", (TODAY,)).fetchone()[0]
print(f"Seuil actif (>= {seuil_actif}) / seuil retraite (< {seuil_retraite})\n")

counts = {"actif": 0, "en_pause": 0, "en_retraite": 0, "jamais_joue": 0}
updates = []
for id_joueur, last_date, is_estime in rows:
    if last_date >= seuil_actif:
        statut = "actif"
    elif last_date >= seuil_retraite:
        statut = "en_pause"
    else:
        statut = "en_retraite"
    counts[statut] += 1
    updates.append((statut, last_date, is_estime, id_joueur))

# joueurs sans aucune date exploitable -> jamais_joue (jamais vus dans participations,
# ou vus mais sans date_tournoi ni expiration exploitable)
all_players = {r[0] for r in conn.execute("SELECT id_fft FROM joueurs").fetchall()}
dated_players = {r[0] for r in rows}
sans_participation = {r[0] for r in conn.execute("SELECT DISTINCT id_joueur FROM participations").fetchall()}
jamais = all_players - dated_players
counts["jamais_joue"] = len(jamais)
vu_mais_non_date = len((sans_participation & jamais))
for id_joueur in jamais:
    updates.append(("jamais_joue", None, None, id_joueur))

print("Distribution :")
for k, v in counts.items():
    print(f"  {k:12s} : {v:,}")
print(f"\n(dont {vu_mais_non_date:,} classés 'jamais_joue' qui ONT des lignes dans "
      f"participations mais aucune date exploitable — pas vraiment 'jamais joué', "
      f"juste 'date inconnue')")

if not APPLY:
    print("\n(dry-run) Rien écrit. Relance avec --apply pour écrire en base.")
    sys.exit(0)

print("\nÉcriture en base (une seule requête, pas de boucle Python)...")
conn.execute("CREATE INDEX IF NOT EXISTS _idx_lastfull ON _last_full(id_joueur)")
conn.execute("BEGIN")
conn.execute(f"""
    UPDATE joueurs SET
        statut_activite = CASE
            WHEN NOT EXISTS (SELECT 1 FROM _last_full lf WHERE lf.id_joueur = joueurs.id_fft) THEN 'jamais_joue'
            WHEN (SELECT lf.last_date FROM _last_full lf WHERE lf.id_joueur = joueurs.id_fft) >= '{seuil_actif}' THEN 'actif'
            WHEN (SELECT lf.last_date FROM _last_full lf WHERE lf.id_joueur = joueurs.id_fft) >= '{seuil_retraite}' THEN 'en_pause'
            ELSE 'en_retraite'
        END,
        dernier_tournoi_date = (SELECT lf.last_date FROM _last_full lf WHERE lf.id_joueur = joueurs.id_fft),
        dernier_tournoi_estime = (SELECT lf.is_estime FROM _last_full lf WHERE lf.id_joueur = joueurs.id_fft)
""")
conn.execute("COMMIT")
print("OK.")
