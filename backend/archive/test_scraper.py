"""
test_scraper.py — Tests de validation post-scrape pour tenup.db
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Vérifie que les données scrapées sont cohérentes :
- colonnes présentes et typées correctement
- pas de décalage de colonnes
- pas de joueurs avec 0 points sur tous leurs tournois
- expiration_date bien calculée
- joueur anonyme non dupliqué en queue

Usage :
    python test_scraper.py              # teste tenup.db
    python test_scraper.py --db tenup_test.db
"""

import sqlite3
import sys
import argparse
import re
from datetime import datetime

# ── Joueurs de référence connus (id_fft, nom, prenom, classement_attendu_approx) ──
# Ajuster si les classements évoluent.
REFERENCE_PLAYERS = [
    # (id_fft,        nom,        prenom,    classement_min, classement_max)
    ('7633273415',    'TISON',    'Jérémy',  100,    500000),   # seed de départ
]

# Tournois de référence (id_tournoi, nb_participations_min_attendu)
REFERENCE_TOURNOIS = [
    ('82580132', 5),   # Tournoi avec Nicolas Rouanet (au moins 5 participants)
]

GREEN = '\033[92m'
RED   = '\033[91m'
YELLOW = '\033[93m'
RESET = '\033[0m'

passed = 0
failed = 0
warned = 0

def ok(msg):
    global passed
    passed += 1
    print(f"  {GREEN}✓{RESET} {msg}")

def fail(msg):
    global failed
    failed += 1
    print(f"  {RED}✗{RESET} {msg}")

def warn(msg):
    global warned
    warned += 1
    print(f"  {YELLOW}⚠{RESET} {msg}")

def run_tests(db_path):
    print(f"\n🔍 Tests sur {db_path}\n")
    con = sqlite3.connect(db_path)
    cur = con.cursor()

    # ── 1. Tables présentes ──────────────────────────────────────
    print("1. Tables et colonnes")
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {r[0] for r in cur.fetchall()}
    for t in ('joueurs', 'participations', 'tournois', 'scrape_queue'):
        if t in tables:
            ok(f"Table '{t}' présente")
        else:
            fail(f"Table '{t}' MANQUANTE")

    # ── 2. Colonnes joueurs ──────────────────────────────────────
    print("\n2. Colonnes joueurs")
    cur.execute("PRAGMA table_info(joueurs)")
    joueurs_cols = {r[1] for r in cur.fetchall()}
    required_joueurs = ['id_fft', 'nom', 'prenom', 'ville', 'echelon', 'sexe',
                        'naissance', 'scraped_at', 'classement', 'club_nom',
                        'meilleur_classement', 'variation_classement', 'classement_date']
    removed_joueurs = ['niveau']
    for col in required_joueurs:
        if col in joueurs_cols:
            ok(f"joueurs.{col} présent")
        else:
            fail(f"joueurs.{col} MANQUANT")
    for col in removed_joueurs:
        if col not in joueurs_cols:
            ok(f"joueurs.{col} bien supprimé")
        else:
            warn(f"joueurs.{col} devrait être supprimé")

    # ── 3. Colonnes participations ───────────────────────────────
    print("\n3. Colonnes participations")
    cur.execute("PRAGMA table_info(participations)")
    part_cols = {r[1] for r in cur.fetchall()}
    required_part = ['id', 'id_joueur', 'id_tournoi', 'partenaire_id', 'partenaire_nom',
                     'date_tournoi', 'position', 'points', 'expiration', 'expiration_date', 'type']
    for col in required_part:
        if col in part_cols:
            ok(f"participations.{col} présent")
        else:
            fail(f"participations.{col} MANQUANT")

    # ── 4. Volumes ───────────────────────────────────────────────
    print("\n4. Volumes de données")
    cur.execute("SELECT COUNT(*) FROM joueurs")
    nb_joueurs = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM participations")
    nb_parts = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM tournois")
    nb_tournois = cur.fetchone()[0]

    if nb_joueurs > 100000:
        ok(f"{nb_joueurs:,} joueurs")
    elif nb_joueurs > 1000:
        warn(f"Seulement {nb_joueurs:,} joueurs (mode test ?)")
    else:
        fail(f"Trop peu de joueurs : {nb_joueurs}")

    if nb_parts > 500000:
        ok(f"{nb_parts:,} participations")
    elif nb_parts > 1000:
        warn(f"Seulement {nb_parts:,} participations")
    else:
        fail(f"Trop peu de participations : {nb_parts}")

    ok(f"{nb_tournois:,} tournois")

    # ── 5. Décalage colonnes (sanity check) ─────────────────────
    print("\n5. Cohérence colonnes (anti-décalage)")
    # classement doit être un entier, pas un texte comme un nom
    cur.execute("SELECT classement FROM joueurs WHERE classement IS NOT NULL LIMIT 100")
    classements = [r[0] for r in cur.fetchall()]
    if classements:
        non_int = [c for c in classements if not isinstance(c, int)]
        if not non_int:
            ok("joueurs.classement : tous entiers")
        else:
            fail(f"joueurs.classement contient des non-entiers : {non_int[:3]}")

        if all(100 <= c <= 9_999_999 for c in classements if c):
            ok(f"joueurs.classement : plage valide (min={min(classements)}, max={max(classements)})")
        else:
            out = [c for c in classements if c and not (100 <= c <= 9_999_999)]
            warn(f"joueurs.classement : valeurs hors plage attendue : {out[:3]}")

    # sexe doit être M ou F (ou vide)
    cur.execute("SELECT DISTINCT sexe FROM joueurs WHERE sexe != '' AND sexe IS NOT NULL LIMIT 20")
    sexes = {r[0] for r in cur.fetchall()}
    unexpected_sexe = sexes - {'M', 'F', 'H', '1', '2'}
    if not unexpected_sexe:
        ok(f"joueurs.sexe : valeurs cohérentes {sexes}")
    else:
        fail(f"joueurs.sexe : valeurs inattendues {unexpected_sexe}")

    # echelon doit ressembler à un chiffre/texte court
    cur.execute("SELECT DISTINCT echelon FROM joueurs WHERE echelon IS NOT NULL AND echelon != '' LIMIT 30")
    echelons = {r[0] for r in cur.fetchall()}
    long_echelons = [e for e in echelons if len(str(e)) > 10]
    if not long_echelons:
        ok(f"joueurs.echelon : valeurs cohérentes (exemples: {list(echelons)[:5]})")
    else:
        fail(f"joueurs.echelon : valeurs trop longues (décalage ?) : {long_echelons[:3]}")

    # naissance doit être une année ou date
    cur.execute("SELECT naissance FROM joueurs WHERE naissance IS NOT NULL AND naissance != '' LIMIT 50")
    naissances = [r[0] for r in cur.fetchall()]
    if naissances:
        bad = [n for n in naissances if not re.match(r'^\d{4}', str(n))]
        if not bad:
            ok(f"joueurs.naissance : format valide (ex: {naissances[0]})")
        else:
            fail(f"joueurs.naissance : format inattendu : {bad[:3]}")

    # ── 6. Points 0 ──────────────────────────────────────────────
    print("\n6. Points zéro (bug scraper)")
    cur.execute("""
        SELECT COUNT(*) FROM participations
        WHERE CAST(points AS INTEGER) = 0 OR points = '0'
    """)
    nb_zero = cur.fetchone()[0]
    ratio = nb_zero / max(nb_parts, 1) * 100
    if ratio < 0.5:
        ok(f"{nb_zero} participations avec points=0 ({ratio:.2f}%) — acceptable")
    else:
        warn(f"{nb_zero} participations avec points=0 ({ratio:.2f}%) — à investiguer")

    # Joueurs avec 100% de leurs tournois à 0 points
    cur.execute("""
        SELECT id_joueur, COUNT(*) as nb
        FROM participations
        GROUP BY id_joueur
        HAVING nb >= 3
          AND SUM(CASE WHEN CAST(points AS INTEGER) > 0 THEN 1 ELSE 0 END) = 0
    """)
    all_zero = cur.fetchall()
    if len(all_zero) < 30:
        ok(f"{len(all_zero)} joueurs avec 3+ tournois tous à 0 points — marginal")
    else:
        warn(f"{len(all_zero)} joueurs avec tous leurs tournois à 0 points — bug scraper ?")

    # ── 7. expiration_date ───────────────────────────────────────
    print("\n7. expiration_date")
    cur.execute("""
        SELECT COUNT(*) FROM participations
        WHERE expiration != '' AND expiration IS NOT NULL
          AND (expiration_date IS NULL OR expiration_date = '')
    """)
    nb_missing_expdate = cur.fetchone()[0]
    if nb_missing_expdate == 0:
        ok("Toutes les expirations ont un expiration_date")
    elif nb_missing_expdate < 1000:
        warn(f"{nb_missing_expdate} participations avec expiration mais sans expiration_date")
    else:
        fail(f"{nb_missing_expdate} participations sans expiration_date — scraper pas à jour ?")

    cur.execute("""
        SELECT expiration_date FROM participations
        WHERE expiration_date IS NOT NULL AND expiration_date != ''
        LIMIT 10
    """)
    sample_dates = [r[0] for r in cur.fetchall()]
    bad_format = [d for d in sample_dates if not re.match(r'^\d{4}-\d{2}$', d)]
    if not bad_format:
        ok(f"expiration_date : format YYYY-MM correct (ex: {sample_dates[:3]})")
    else:
        fail(f"expiration_date : format incorrect : {bad_format[:3]}")

    # ── 8. Joueur anonyme ────────────────────────────────────────
    print("\n8. Joueur anonyme")
    cur.execute("SELECT statut FROM scrape_queue WHERE id_fft='10011859240'")
    row = cur.fetchone()
    if row:
        if row[0] == 'done':
            ok("Joueur Anonyme (10011859240) marqué 'done' — ne sera pas re-scrapé")
        else:
            warn(f"Joueur Anonyme (10011859240) en statut '{row[0]}' — devrait être 'done'")
    else:
        warn("Joueur Anonyme (10011859240) absent de la queue — normal si jamais rencontré")

    cur.execute("SELECT COUNT(*) FROM participations WHERE partenaire_nom='Joueur Anonyme'")
    nb_anon = cur.fetchone()[0]
    ok(f"{nb_anon:,} participations avec partenaire anonyme")

    # ── 9. Scrape queue ──────────────────────────────────────────
    print("\n9. Scrape queue")
    cur.execute("SELECT statut, COUNT(*) FROM scrape_queue GROUP BY statut ORDER BY COUNT(*) DESC")
    for statut, cnt in cur.fetchall():
        if statut == 'error':
            warn(f"  scrape_queue '{statut}': {cnt:,}")
        else:
            ok(f"  scrape_queue '{statut}': {cnt:,}")

    # ── 10. Joueurs de référence ─────────────────────────────────
    print("\n10. Joueurs de référence")
    for id_fft, nom, prenom, cl_min, cl_max in REFERENCE_PLAYERS:
        cur.execute("""
            SELECT nom, prenom, classement, echelon
            FROM joueurs WHERE id_fft=?
        """, (id_fft,))
        row = cur.fetchone()
        if not row:
            fail(f"Joueur référence {prenom} {nom} ({id_fft}) introuvable")
            continue
        r_nom, r_prenom, r_cl, r_echelon = row
        if cl_min <= (r_cl or 0) <= cl_max:
            ok(f"{r_prenom} {r_nom} — classement {r_cl} (échelon {r_echelon})")
        else:
            warn(f"{r_prenom} {r_nom} — classement {r_cl} hors plage [{cl_min}, {cl_max}]")

    # ── 11. Tournois de référence ────────────────────────────────
    print("\n11. Tournois de référence")
    for id_tournoi, nb_min in REFERENCE_TOURNOIS:
        cur.execute("SELECT COUNT(*) FROM participations WHERE id_tournoi=?", (id_tournoi,))
        nb = cur.fetchone()[0]
        if nb >= nb_min:
            ok(f"Tournoi {id_tournoi} — {nb} participations")
        else:
            fail(f"Tournoi {id_tournoi} — seulement {nb} participations (attendu >= {nb_min})")

        # Vérifier que les points ne sont pas tous à 0
        cur.execute("""
            SELECT COUNT(*) FROM participations
            WHERE id_tournoi=? AND (points IS NULL OR points='0' OR points='')
        """, (id_tournoi,))
        nb_zero_t = cur.fetchone()[0]
        if nb_zero_t == 0:
            ok(f"Tournoi {id_tournoi} — aucun points=0")
        elif nb_zero_t < nb // 2:
            warn(f"Tournoi {id_tournoi} — {nb_zero_t}/{nb} participations avec points=0")
        else:
            fail(f"Tournoi {id_tournoi} — {nb_zero_t}/{nb} participations avec points=0 (bug ?)")

    # ── Résumé ───────────────────────────────────────────────────
    con.close()
    print(f"\n{'─'*50}")
    print(f"Résultat : {GREEN}{passed} OK{RESET}  {RED}{failed} ÉCHEC{RESET}  {YELLOW}{warned} AVERTISSEMENTS{RESET}")
    if failed > 0:
        print(f"{RED}❌ Tests échoués — vérifier le scraper avant de lancer mai{RESET}")
        sys.exit(1)
    elif warned > 0:
        print(f"{YELLOW}⚠  Quelques avertissements — inspecter avant de lancer{RESET}")
    else:
        print(f"{GREEN}✅ Tout OK — prêt pour le scrape de mai{RESET}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', default='tenup.db', help='Chemin vers la DB à tester')
    args = parser.parse_args()

    import os
    db_path = args.db if os.path.isabs(args.db) else os.path.join(os.path.dirname(__file__), args.db)
    run_tests(db_path)
