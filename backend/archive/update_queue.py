"""
update_queue.py — Mise à jour mensuelle
────────────────────────────────────────
Identifie les joueurs actifs et les remet en queue pour re-scraping.

Joueur "actif" = a joué un tournoi dans les 6 derniers mois
                 ET n'a pas été re-scrapé depuis plus de 30 jours.

Usage :
    python update_queue.py              → affiche les stats + ajoute en queue
    python update_queue.py --dry-run    → aperçu seulement, n'écrit rien
"""

import sqlite3, argparse, os
from datetime import datetime, timedelta

DB_FILE = os.path.join(os.path.dirname(__file__), 'tenup.db')

parser = argparse.ArgumentParser()
parser.add_argument('--dry-run', action='store_true', help='Aperçu sans écrire')
parser.add_argument('--mois',    type=int, default=6,  help='Fenêtre activité (mois, défaut: 6)')
parser.add_argument('--jours',   type=int, default=30, help='Délai min depuis dernier scraping (jours, défaut: 30)')
args = parser.parse_args()

conn = sqlite3.connect(DB_FILE, isolation_level=None)
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA busy_timeout=10000")

# ── Dates seuils ──────────────────────────────────────────────────
cutoff_scrape  = (datetime.now() - timedelta(days=args.jours)).isoformat()
cutoff_tournoi = (datetime.now() - timedelta(days=args.mois * 30)).strftime('%d/%m/%Y')

print(f"\n{'='*55}")
print(f"  Mise à jour mensuelle — {datetime.now().strftime('%d/%m/%Y %H:%M')}")
print(f"{'='*55}")
print(f"  Actif si dernier tournoi  > {cutoff_tournoi}")
print(f"  À re-scraper si scrapé  < il y a {args.jours} jours")
print()

# ── Joueurs actifs à re-scraper ───────────────────────────────────
# Note : date_tournoi est au format DD/MM/YYYY, on compare en texte
# ce qui fonctionne car on filtre via Python après récupération
rows = conn.execute("""
    SELECT
        j.id_fft,
        j.prenom || ' ' || j.nom    AS nom_complet,
        MAX(p.date_tournoi)         AS dernier_tournoi,
        j.scraped_at
    FROM joueurs j
    JOIN participations p ON p.id_joueur = j.id_fft
    WHERE j.scraped_at < ?
    GROUP BY j.id_fft
    ORDER BY j.scraped_at ASC
""", (cutoff_scrape,)).fetchall()

# Filtrer par date de tournoi (format DD/MM/YYYY → parse)
actifs = []
for id_fft, nom, dernier, scraped in rows:
    try:
        dt = datetime.strptime(dernier, '%d/%m/%Y')
        if dt >= datetime.now() - timedelta(days=args.mois * 30):
            actifs.append((id_fft, nom, dernier, scraped))
    except:
        pass

print(f"  Joueurs en base           : {conn.execute('SELECT COUNT(*) FROM joueurs').fetchone()[0]}")
print(f"  Joueurs actifs trouvés    : {len(actifs)}")
print(f"  Mode                      : {'DRY RUN (rien écrit)' if args.dry_run else 'ÉCRITURE'}")
print()

if not actifs:
    print("  ✅ Rien à mettre à jour.")
    conn.close()
    exit()

# Aperçu des 10 premiers
print(f"  {'NOM':<30} {'DERNIER TOURNOI':<18} {'SCRAPÉ LE'}")
print(f"  {'-'*70}")
for id_fft, nom, dernier, scraped in actifs[:10]:
    scraped_short = scraped[:10] if scraped else '?'
    print(f"  {nom:<30} {dernier:<18} {scraped_short}")
if len(actifs) > 10:
    print(f"  ... et {len(actifs)-10} autres")

if args.dry_run:
    print(f"\n  [DRY RUN] {len(actifs)} joueurs seraient ajoutés en queue.")
    conn.close()
    exit()

# ── Ajout en queue ────────────────────────────────────────────────
print()
added = updated = skipped = 0

for id_fft, nom, dernier, scraped in actifs:
    exists = conn.execute(
        "SELECT statut FROM scrape_queue WHERE id_fft=?", (id_fft,)
    ).fetchone()

    if exists:
        if exists[0] in ('pending', 'processing', 'update'):
            skipped += 1
            continue
        # Déjà scrapé (done/error) → remettre en update
        conn.execute(
            "UPDATE scrape_queue SET statut='update', scraped_at=NULL, error=NULL, worker_id=NULL WHERE id_fft=?",
            (id_fft,)
        )
        updated += 1
    else:
        conn.execute(
            "INSERT INTO scrape_queue (id_fft, statut, added_at) VALUES (?, 'update', ?)",
            (id_fft, datetime.now().isoformat())
        )
        added += 1

print(f"  ✅ {added} ajoutés, {updated} remis en queue, {skipped} déjà en attente")
print(f"\n  Lance maintenant : python scraper.py --limit 1000")
print()
conn.close()
