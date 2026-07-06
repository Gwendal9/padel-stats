"""
verif_scrape_vs_csv.py — Vérification du scrape vs classement officiel FFT

Compare les données scrapées (tenup.db) avec les CSV officiels de classement
(joueurs_padel_H.csv / joueurs_padel_F.csv) pour détecter :

  1. Couverture     : % de joueurs CSV déjà scrapés ce mois
  2. Manquants      : joueurs dans le CSV mais pas encore scrapés
  3. Classement     : écarts entre DB et CSV pour les joueurs scrapés
  4. Nouveaux       : joueurs dans le CSV absents de la DB
  5. Fantômes       : joueurs scrapés absents du CSV (découverts via partenaires)

Usage :
    python verif_scrape_vs_csv.py                           # vérifie le mois courant
    python verif_scrape_vs_csv.py --mois 2026-05            # force le mois
    python verif_scrape_vs_csv.py --detail                  # affiche les écarts un par un
    python verif_scrape_vs_csv.py --export ecarts_mai.csv   # exporte les écarts dans un CSV
"""
import os
import sys
import csv
import sqlite3
from datetime import datetime

BASE_DIR     = os.path.dirname(__file__)
DB_FILE      = os.path.join(BASE_DIR, 'tenup.db')
CSV_H        = os.path.join(BASE_DIR, 'joueurs_padel_H.csv')
CSV_F        = os.path.join(BASE_DIR, 'joueurs_padel_F.csv')
CSV_LEGACY   = os.path.join(BASE_DIR, 'joueurs_padel.csv')

# ── Args ──────────────────────────────────────────────────────────────
DETAIL       = '--detail' in sys.argv
MOIS_COURANT = datetime.now().strftime('%Y-%m')
EXPORT_FILE  = None

args = sys.argv[1:]
if '--mois' in args:
    idx = args.index('--mois')
    if idx + 1 < len(args):
        MOIS_COURANT = args[idx + 1]
if '--export' in args:
    idx = args.index('--export')
    if idx + 1 < len(args):
        EXPORT_FILE = args[idx + 1]

# ── Chargement CSV ────────────────────────────────────────────────────
def load_csv_classement():
    """Charge H + F (ou legacy) depuis les CSV officiels."""
    sources = []
    if os.path.exists(CSV_H):
        sources.append((CSV_H, 'H'))
    if os.path.exists(CSV_F):
        sources.append((CSV_F, 'F'))
    if not sources and os.path.exists(CSV_LEGACY):
        sources.append((CSV_LEGACY, None))

    if not sources:
        print("❌ Aucun CSV trouvé (joueurs_padel_H.csv / joueurs_padel_F.csv)")
        sys.exit(1)

    rows = {}
    for path, sexe_override in sources:
        with open(path, encoding='utf-8-sig') as f:
            for r in csv.DictReader(f):
                id_fft = str(r.get('idCrm', '')).strip()
                if not id_fft:
                    continue
                try:
                    classement = int(float(r['classement'])) if r.get('classement') else None
                except (ValueError, TypeError):
                    classement = None
                try:
                    evolution = int(float(r['evolution'])) if r.get('evolution') not in (None, '', 'nan') else None
                except (ValueError, TypeError):
                    evolution = None
                try:
                    meilleur = int(float(r['meilleurClassement'])) if r.get('meilleurClassement') not in (None, '', 'nan') else None
                except (ValueError, TypeError):
                    meilleur = None

                rows[id_fft] = {
                    'id_fft':     id_fft,
                    'nom':        r.get('nom', '').strip(),
                    'prenom':     r.get('prenom', '').strip(),
                    'club':       r.get('club', '').strip(),
                    'position':   r.get('position', ''),
                    'classement': classement,
                    'evolution':  evolution,
                    'meilleur':   meilleur,
                    'sexe':       sexe_override or r.get('sexe', '').strip().upper(),
                    'ligue':      r.get('ligue', '').strip(),
                    'points':     r.get('points', ''),
                    'nb_tournois': r.get('nombreTournoisJoues', ''),
                }
        fname = os.path.basename(path)
        print(f"  📄 {fname} → {sum(1 for v in rows.values() if (sexe_override or '') == (v['sexe'] or '')):,} joueurs")

    return rows


# ── Chargement DB ─────────────────────────────────────────────────────
def load_db_joueurs(conn, mois):
    """
    Charge tous les joueurs de la DB avec leur état de scrape.
    `scraped_this_month` = scraped_at commence par le mois cible.
    """
    rows = {}
    for r in conn.execute("""
        SELECT j.id_fft, j.nom, j.prenom, j.sexe,
               j.classement, j.variation_classement, j.meilleur_classement,
               j.classement_date, j.scraped_at, j.club_nom,
               q.statut as queue_statut
        FROM joueurs j
        LEFT JOIN scrape_queue q ON q.id_fft = j.id_fft
    """).fetchall():
        scraped_at = r[8] or ''
        rows[r[0]] = {
            'id_fft':       r[0],
            'nom':          r[1] or '',
            'prenom':       r[2] or '',
            'sexe':         r[3] or '',
            'classement':   r[4],
            'variation':    r[5],
            'meilleur':     r[6],
            'classement_date': r[7] or '',
            'scraped_at':   scraped_at,
            'club_nom':     r[9] or '',
            'queue_statut': r[10] or '',
            'scraped_this_month': scraped_at.startswith(mois),
        }
    return rows


# ── Comparaison ───────────────────────────────────────────────────────
def compare(csv_data, db_data, mois):
    csv_ids = set(csv_data.keys())
    db_ids  = set(db_data.keys())

    # 1. Couverture : joueurs CSV scrapés ce mois
    scraped_ok   = [id_ for id_ in csv_ids if id_ in db_data and db_data[id_]['scraped_this_month']]
    not_scraped  = [id_ for id_ in csv_ids if id_ not in db_data or not db_data[id_]['scraped_this_month']]

    # 2. Nouveaux : dans CSV mais jamais en DB
    new_ids      = [id_ for id_ in csv_ids if id_ not in db_ids]

    # 3. En attente : dans CSV, en DB mais pas encore scrapés ce mois
    in_queue     = [id_ for id_ in not_scraped if id_ in db_data]

    # 4. Écarts de classement sur les joueurs scrapés ce mois
    ecarts       = []
    for id_ in scraped_ok:
        c = csv_data[id_]
        d = db_data[id_]
        if c['classement'] is None or d['classement'] is None:
            continue
        delta = abs(c['classement'] - d['classement'])
        if delta > 0:
            ecarts.append({
                'id_fft':     id_,
                'nom':        d['nom'],
                'prenom':     d['prenom'],
                'sexe':       c['sexe'],
                'csv_cl':     c['classement'],
                'db_cl':      d['classement'],
                'delta':      delta,
                'csv_evol':   c['evolution'],
                'db_evol':    d['variation'],
                'csv_club':   c['club'],
                'db_club':    d['club_nom'],
            })

    # 5. Fantômes : scrapés (scraped_at non null) mais absents du CSV
    fantomes = [id_ for id_ in db_ids if id_ not in csv_ids
                and db_data[id_]['scraped_at']]

    return {
        'csv_total':     len(csv_ids),
        'scraped_ok':    scraped_ok,
        'not_scraped':   not_scraped,
        'new_ids':       new_ids,
        'in_queue':      in_queue,
        'ecarts':        sorted(ecarts, key=lambda x: x['delta'], reverse=True),
        'fantomes':      fantomes,
    }


# ── Affichage ─────────────────────────────────────────────────────────
def print_report(result, csv_data, db_data, mois):
    total    = result['csv_total']
    ok       = len(result['scraped_ok'])
    waiting  = len(result['in_queue'])
    new      = len(result['new_ids'])
    ecarts   = result['ecarts']
    fantomes = result['fantomes']

    coverage = ok / total * 100 if total else 0

    print(f"\n{'━'*60}")
    print(f"  BILAN SCRAPE vs CSV OFFICIEL — {mois}")
    print(f"{'━'*60}\n")

    # ── Couverture ────────────────────────────────────────────────────
    bar_len = 40
    filled  = int(bar_len * coverage / 100)
    bar     = '█' * filled + '░' * (bar_len - filled)
    print(f"COUVERTURE DU MOIS")
    print(f"  [{bar}] {coverage:.1f}%")
    print(f"  {ok:>7,} / {total:,} joueurs classés scrapés ce mois ({mois})")
    print(f"  {total - ok:>7,} joueurs restants à scraper")
    print()

    # ── Détail en attente ─────────────────────────────────────────────
    in_done   = sum(1 for id_ in result['not_scraped'] if id_ in db_data and db_data[id_]['queue_statut'] == 'done')
    in_pending = sum(1 for id_ in result['not_scraped'] if id_ in db_data and db_data[id_]['queue_statut'] == 'pending')
    in_err    = sum(1 for id_ in result['not_scraped'] if id_ in db_data and db_data[id_]['queue_statut'] == 'error')

    print(f"STATUT DES {total - ok:,} NON ENCORE SCRAPÉS CE MOIS")
    print(f"  {in_pending:>7,}  en queue pending  (sera traité par le scraper)")
    print(f"  {in_done:>7,}  statut done mais mois précédent (monthly_refresh pas encore lancé)")
    print(f"  {in_err:>7,}  en erreur")
    print(f"  {new:>7,}  nouveaux joueurs (dans CSV, absents de la DB)")
    print()

    # ── Classements ───────────────────────────────────────────────────
    print(f"COHÉRENCE DES CLASSEMENTS (joueurs scrapés ce mois)")
    match   = ok - len(ecarts)
    pct_ok  = match / ok * 100 if ok else 0
    print(f"  {match:>7,}  classements identiques CSV ↔ DB  ({pct_ok:.1f}%)")
    print(f"  {len(ecarts):>7,}  écarts détectés")

    if ecarts:
        # Distribution des écarts
        d1  = sum(1 for e in ecarts if e['delta'] == 1)
        d2_5 = sum(1 for e in ecarts if 2 <= e['delta'] <= 5)
        d6p = sum(1 for e in ecarts if e['delta'] > 5)
        print(f"           dont  Δ=1 places  : {d1:,}")
        print(f"                 Δ 2-5 places : {d2_5:,}")
        print(f"                 Δ > 5 places : {d6p:,}")

        if DETAIL and ecarts:
            print(f"\n  Top écarts (Δ > 5) :")
            for e in ecarts[:20]:
                if e['delta'] <= 5:
                    break
                print(f"    {e['prenom']} {e['nom']} ({e['sexe']})  "
                      f"CSV #{e['csv_cl']} → DB #{e['db_cl']}  (Δ={e['delta']})")
    print()

    # ── Fantômes ──────────────────────────────────────────────────────
    h_f = sum(1 for id_ in fantomes if db_data[id_]['sexe'] == 'H')
    f_f = sum(1 for id_ in fantomes if db_data[id_]['sexe'] == 'F')
    print(f"JOUEURS DÉCOUVERTS VIA PARTENAIRES (absents du classement officiel)")
    print(f"  {len(fantomes):>7,} joueurs scrapés hors classement (H:{h_f} F:{f_f})")
    print(f"           → joueurs non classés, étrangers, ou perdus depuis")
    print()

    # ── Sexe breakdown ────────────────────────────────────────────────
    ok_h = sum(1 for id_ in result['scraped_ok'] if csv_data[id_]['sexe'] == 'H')
    ok_f = sum(1 for id_ in result['scraped_ok'] if csv_data[id_]['sexe'] == 'F')
    tot_h = sum(1 for v in csv_data.values() if v['sexe'] == 'H')
    tot_f = sum(1 for v in csv_data.values() if v['sexe'] == 'F')
    print(f"DÉTAIL PAR SEXE")
    print(f"  Hommes  : {ok_h:,} / {tot_h:,}  ({ok_h/tot_h*100:.1f}%)")
    print(f"  Femmes  : {ok_f:,} / {tot_f:,}  ({ok_f/tot_f*100:.1f}%)")
    print()

    print(f"{'━'*60}")

    # ── Export ────────────────────────────────────────────────────────
    if EXPORT_FILE and ecarts:
        out = os.path.join(BASE_DIR, EXPORT_FILE)
        with open(out, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=[
                'id_fft','nom','prenom','sexe',
                'csv_classement','db_classement','delta',
                'csv_evolution','db_variation',
                'csv_club','db_club'
            ])
            writer.writeheader()
            for e in ecarts:
                writer.writerow({
                    'id_fft':        e['id_fft'],
                    'nom':           e['nom'],
                    'prenom':        e['prenom'],
                    'sexe':          e['sexe'],
                    'csv_classement': e['csv_cl'],
                    'db_classement': e['db_cl'],
                    'delta':         e['delta'],
                    'csv_evolution': e['csv_evol'],
                    'db_variation':  e['db_evol'],
                    'csv_club':      e['csv_club'],
                    'db_club':       e['db_club'],
                })
        print(f"\n💾 Écarts exportés : {out}  ({len(ecarts)} lignes)")


# ── Main ──────────────────────────────────────────────────────────────
def main():
    print(f"=== Vérification scrape vs CSV officiel — {MOIS_COURANT} ===\n")

    print("Chargement des CSV :")
    csv_data = load_csv_classement()
    print(f"  Total CSV : {len(csv_data):,} joueurs uniques\n")

    if not os.path.exists(DB_FILE):
        print(f"❌ Base introuvable : {DB_FILE}")
        sys.exit(1)

    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.execute("PRAGMA query_only=1")

    print("Chargement de la DB...")
    db_data = load_db_joueurs(conn, MOIS_COURANT)
    print(f"  Total DB  : {len(db_data):,} joueurs")
    conn.close()

    result = compare(csv_data, db_data, MOIS_COURANT)
    print_report(result, csv_data, db_data, MOIS_COURANT)


if __name__ == '__main__':
    main()
