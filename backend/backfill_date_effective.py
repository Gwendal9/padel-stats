"""
backfill_date_effective.py — Calcule une date "effective" pour chaque participation,
utilisable partout où le code lisait `date_tournoi` jusqu'ici.

Contexte : depuis la bascule vers le scraper JSON (mars 2026), `date_tournoi` n'est
plus jamais rempli par la FFT (voir NOTE_date_tournoi_et_pipeline.md — le champ n'existe
simplement plus dans l'API bilan). ~50% des participations (tout ce qui est post-03/2026)
ont donc date_tournoi = NULL, ce qui cassait silencieusement tous les tris/filtres qui se
basaient dessus (stats mensuelles, listes de tournois, activité club, parcours joueur...).

Ce script ajoute 2 colonnes à `participations` :
  - date_effective  : format DD/MM/YYYY, calculée par ordre de préférence :
      1. date_tournoi si elle existe (vraie date FFT) -> date_estimee = 0
      2. sinon, si premiere_vue_mois est connu (colonne alimentée par scraper_json.py
         depuis 09/2026, mois où la participation a été vue pour la 1re fois) :
         ESTIMATION = premiere_vue_mois - 1 mois (jour fixé au 15).
         Constat de Gwendal (09/2026) : un scrape fait en mois M ramène en réalité les
         tournois joués en mois M-1 (le temps que la FFT publie les résultats dans le
         bilan) -> plus précis que l'estimation via expiration une fois qu'on a cette
         info de scraping, à privilégier dès qu'elle est disponible.
      3. sinon (participations plus anciennes, avant l'ajout de premiere_vue_mois) :
         ESTIMATION = expiration - 365 jours (écart médian vérifié sur 493 lignes ayant
         les deux dates : 363 jours, min 349, max 379 — même formule que compute_activite.py).
  - date_estimee    : 1 si date_effective est une estimation (cas 2 ou 3 ci-dessus),
                       0 si c'est la vraie date FFT (cas 1). À utiliser côté front pour
                       afficher un badge "estimé" plutôt que de faire croire à une date exacte.

Usage :
    python backfill_date_effective.py            # simulation (dry-run)
    python backfill_date_effective.py --apply     # exécution réelle
"""
import sqlite3, sys, os

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tenup.db')
APPLY = '--apply' in sys.argv

conn = sqlite3.connect(DB, isolation_level=None)

for m in [
    "ALTER TABLE participations ADD COLUMN date_effective TEXT",
    "ALTER TABLE participations ADD COLUMN date_estimee INTEGER",
]:
    try:
        conn.execute(m)
    except Exception:
        pass

print(f"=== backfill_date_effective — {'EXÉCUTION' if APPLY else 'SIMULATION (dry-run)'} ===\n")

total = conn.execute("SELECT COUNT(*) FROM participations").fetchone()[0]
reel = conn.execute(
    "SELECT COUNT(*) FROM participations WHERE date_tournoi IS NOT NULL AND length(date_tournoi) >= 10"
).fetchone()[0]
via_scrape = conn.execute(
    "SELECT COUNT(*) FROM participations WHERE (date_tournoi IS NULL OR length(date_tournoi) < 10) "
    "AND premiere_vue_mois IS NOT NULL AND length(premiere_vue_mois) = 7"
).fetchone()[0]
via_expiration = conn.execute(
    "SELECT COUNT(*) FROM participations WHERE (date_tournoi IS NULL OR length(date_tournoi) < 10) "
    "AND (premiere_vue_mois IS NULL OR length(premiere_vue_mois) != 7) "
    "AND expiration IS NOT NULL AND length(expiration) >= 10"
).fetchone()[0]
sans_date = total - reel - via_scrape - via_expiration

print(f"Participations totales                        : {total:,}")
print(f"  -> date réelle (date_tournoi)                : {reel:,}")
print(f"  -> estimée via premiere_vue_mois - 1 mois     : {via_scrape:,}")
print(f"  -> estimée via expiration - 365j              : {via_expiration:,}")
print(f"  -> sans aucune date exploitable                : {sans_date:,}")

if not APPLY:
    print("\n(dry-run) Rien écrit. Relance avec --apply pour écrire en base.")
    sys.exit(0)

import time
BUDGET_S = float(os.environ.get("BACKFILL_BUDGET_S", "150"))
BATCH = 20000
t_start = time.time()

_SRC = (
    "(  (date_tournoi IS NOT NULL AND length(date_tournoi) >= 10) "
    "OR (premiere_vue_mois IS NOT NULL AND length(premiere_vue_mois) = 7) "
    "OR (expiration IS NOT NULL AND length(expiration) >= 10) )"
)

remaining_before = conn.execute(
    f"SELECT COUNT(*) FROM participations WHERE date_effective IS NULL AND {_SRC}"
).fetchone()[0]
print(f"Restant à traiter avant ce passage : {remaining_before:,} (budget {BUDGET_S:.0f}s, lots de {BATCH:,})")

# NB: pas de grosse liste IN (?,?,?...) -> risque de dépasser SQLITE_MAX_VARIABLE_NUMBER.
# La sous-requête LIMIT ne prend qu'un seul paramètre lié, quelle que soit la taille du lot.
UPDATE_SQL = f"""
    UPDATE participations SET
        date_effective = CASE
            WHEN date_tournoi IS NOT NULL AND length(date_tournoi) >= 10 THEN date_tournoi
            WHEN premiere_vue_mois IS NOT NULL AND length(premiere_vue_mois) = 7 THEN
                substr(date(premiere_vue_mois || '-15', '-1 months'), 9, 2) || '/' ||
                substr(date(premiere_vue_mois || '-15', '-1 months'), 6, 2) || '/' ||
                substr(date(premiere_vue_mois || '-15', '-1 months'), 1, 4)
            ELSE substr(date(substr(expiration,1,10), '-365 days'), 9, 2) || '/' ||
                 substr(date(substr(expiration,1,10), '-365 days'), 6, 2) || '/' ||
                 substr(date(substr(expiration,1,10), '-365 days'), 1, 4)
        END,
        date_estimee = CASE WHEN date_tournoi IS NOT NULL AND length(date_tournoi) >= 10 THEN 0 ELSE 1 END
    WHERE id IN (
        SELECT id FROM participations
        WHERE date_effective IS NULL AND {_SRC}
        LIMIT ?
    )
"""

n_done = 0
while True:
    if time.time() - t_start > BUDGET_S:
        print(f"\n⏱️  Budget de temps atteint, arrêt propre après {n_done:,} lignes ce passage.")
        break
    conn.execute("BEGIN")
    cur = conn.execute(UPDATE_SQL, (BATCH,))
    n_batch = cur.rowcount
    conn.execute("COMMIT")
    if n_batch <= 0:
        print(f"\n✓ Terminé — plus aucune ligne à traiter ({n_done:,} lignes ce passage).")
        break
    n_done += n_batch
    print(f"  ... {n_done:,} lignes traitées ce passage ({time.time()-t_start:.0f}s écoulées)")

n_eff = conn.execute("SELECT COUNT(*) FROM participations WHERE date_effective IS NOT NULL").fetchone()[0]
n_est = conn.execute("SELECT COUNT(*) FROM participations WHERE date_estimee = 1").fetchone()[0]
n_remaining = conn.execute(
    f"SELECT COUNT(*) FROM participations WHERE date_effective IS NULL AND {_SRC}"
).fetchone()[0]
print(f"\nÉtat global : date_effective renseignée sur {n_eff:,} lignes (dont {n_est:,} estimées). Restant : {n_remaining:,}")
if n_remaining:
    print("Relance le script (--apply) pour continuer — il reprend automatiquement où il s'est arrêté.")

# Vérif rapide : cohérence format sur un échantillon (dates via premiere_vue_mois si dispo, sinon expiration)
sample = conn.execute(
    "SELECT date_tournoi, premiere_vue_mois, expiration, date_effective, date_estimee FROM participations "
    "WHERE date_estimee = 1 AND premiere_vue_mois IS NOT NULL LIMIT 3"
).fetchall()
print("\nÉchantillon (dates estimées via premiere_vue_mois) :")
for row in sample:
    print(" ", row)
sample2 = conn.execute(
    "SELECT date_tournoi, premiere_vue_mois, expiration, date_effective, date_estimee FROM participations "
    "WHERE date_estimee = 1 AND premiere_vue_mois IS NULL LIMIT 3"
).fetchall()
print("\nÉchantillon (dates estimées via expiration, fallback) :")
for row in sample2:
    print(" ", row)
