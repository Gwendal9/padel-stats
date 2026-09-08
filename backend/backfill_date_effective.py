"""
backfill_date_effective.py — Calcule une date "effective" pour chaque participation,
utilisable partout où le code lisait `date_tournoi` jusqu'ici.

Contexte : depuis la bascule vers le scraper JSON (mars 2026), `date_tournoi` n'est
plus jamais rempli par la FFT (voir NOTE_date_tournoi_et_pipeline.md — le champ n'existe
simplement plus dans l'API bilan). Seule `expiration` (date_tournoi + ~12 mois côté FFT)
reste disponible. Résultat : ~50% des participations (tout ce qui est post-03/2026) ont
date_tournoi = NULL, ce qui casse silencieusement tous les tris/filtres qui se basaient
dessus (stats mensuelles, listes de tournois, activité club, parcours joueur, etc.)

Ce script ajoute 2 colonnes à `participations` :
  - date_effective  : date_tournoi si elle existe (format DD/MM/YYYY, identique à
                       date_tournoi pour rester un remplacement direct), sinon
                       une ESTIMATION = expiration - 365 jours (écart médian vérifié
                       sur 493 lignes ayant les deux dates : 363 jours, min 349, max 379
                       — même formule que compute_activite.py).
  - date_estimee    : 1 si date_effective est une estimation (date_tournoi absent),
                       0 si c'est la vraie date FFT. À utiliser côté front pour afficher
                       un badge "estimé" plutôt que de faire croire à une date exacte.

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
estime = conn.execute(
    "SELECT COUNT(*) FROM participations WHERE (date_tournoi IS NULL OR length(date_tournoi) < 10) "
    "AND expiration IS NOT NULL AND length(expiration) >= 10"
).fetchone()[0]
sans_date = total - reel - estime

print(f"Participations totales      : {total:,}")
print(f"  -> date réelle (date_tournoi)      : {reel:,}")
print(f"  -> date estimée (expiration-365j)  : {estime:,}")
print(f"  -> sans aucune date exploitable     : {sans_date:,}")

if not APPLY:
    print("\n(dry-run) Rien écrit. Relance avec --apply pour écrire en base.")
    sys.exit(0)

import time
BUDGET_S = float(os.environ.get("BACKFILL_BUDGET_S", "150"))
BATCH = 20000
t_start = time.time()

remaining_before = conn.execute(
    "SELECT COUNT(*) FROM participations WHERE date_effective IS NULL AND "
    "(  (date_tournoi IS NOT NULL AND length(date_tournoi) >= 10) "
    "OR (expiration IS NOT NULL AND length(expiration) >= 10) )"
).fetchone()[0]
print(f"Restant à traiter avant ce passage : {remaining_before:,} (budget {BUDGET_S:.0f}s, lots de {BATCH:,})")

# NB: pas de grosse liste IN (?,?,?...) -> risque de dépasser SQLITE_MAX_VARIABLE_NUMBER.
# La sous-requête LIMIT ne prend qu'un seul paramètre lié, quelle que soit la taille du lot.
UPDATE_SQL = f"""
    UPDATE participations SET
        date_effective = CASE
            WHEN date_tournoi IS NOT NULL AND length(date_tournoi) >= 10 THEN date_tournoi
            ELSE substr(date(substr(expiration,1,10), '-365 days'), 9, 2) || '/' ||
                 substr(date(substr(expiration,1,10), '-365 days'), 6, 2) || '/' ||
                 substr(date(substr(expiration,1,10), '-365 days'), 1, 4)
        END,
        date_estimee = CASE WHEN date_tournoi IS NOT NULL AND length(date_tournoi) >= 10 THEN 0 ELSE 1 END
    WHERE id IN (
        SELECT id FROM participations
        WHERE date_effective IS NULL AND
              (  (date_tournoi IS NOT NULL AND length(date_tournoi) >= 10)
              OR (expiration IS NOT NULL AND length(expiration) >= 10) )
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
    "SELECT COUNT(*) FROM participations WHERE date_effective IS NULL AND "
    "(  (date_tournoi IS NOT NULL AND length(date_tournoi) >= 10) "
    "OR (expiration IS NOT NULL AND length(expiration) >= 10) )"
).fetchone()[0]
print(f"\nÉtat global : date_effective renseignée sur {n_eff:,} lignes (dont {n_est:,} estimées). Restant : {n_remaining:,}")
if n_remaining:
    print("Relance le script (--apply) pour continuer — il reprend automatiquement où il s'est arrêté.")

# Vérif rapide : cohérence format sur un échantillon
sample = conn.execute(
    "SELECT date_tournoi, expiration, date_effective, date_estimee FROM participations "
    "WHERE date_estimee = 1 LIMIT 3"
).fetchall()
print("\nÉchantillon (dates estimées) :")
for row in sample:
    print(" ", row)
