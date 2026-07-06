"""
match_partenaires.py — Résout les partenaires de jeu : partenaire_nom -> partenaire_id (id_fft).

Le bilan ne donne le partenaire que par nom+prénom (pas d'id). Pour construire le graphe
de co-participation et le suggesteur, il faut retrouver l'id_fft du partenaire.

Méthode :
  - normalisation du nom (majuscules, sans accents, espaces compactés)
  - index des joueurs par nom normalisé
  - matching DANS LE BON POOL H/F déduit du type d'épreuve :
        DM (Double Messieurs) -> partenaire Homme
        DD (Double Dames)     -> partenaire Femme
        DX (Mixte)            -> partenaire = sexe opposé au joueur
        (autre/inconnu)        -> on cherche dans les deux pools
  - on n'assigne que si le candidat est UNIQUE dans le pool (sinon : homonyme ambigu, laissé vide)

⚠️ DRY-RUN par défaut (compte sans écrire). Ajoute --apply pour écrire les partenaire_id.

Usage :
    python match_partenaires.py            # simulation + stats
    python match_partenaires.py --apply     # écrit les partenaire_id
"""
import sqlite3, sys, os, unicodedata
from collections import defaultdict

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tenup.db')
APPLY = '--apply' in sys.argv


def norm(s):
    """Majuscules, sans accents, espaces/tirets compactés."""
    if not s:
        return ''
    s = unicodedata.normalize('NFKD', s)
    s = ''.join(c for c in s if not unicodedata.combining(c))
    s = s.upper().replace('-', ' ')
    return ' '.join(s.split())


def partenaire_sexe(type_epreuve, sexe_joueur):
    """Déduit le sexe attendu du partenaire selon l'épreuve et le sexe du joueur."""
    t = (type_epreuve or '').upper()
    if t == 'DM':
        return 'H'
    if t == 'DD':
        return 'F'
    if t == 'DX':  # mixte -> sexe opposé
        return 'F' if sexe_joueur == 'H' else ('H' if sexe_joueur == 'F' else None)
    return None  # inconnu -> les deux pools


def main():
    c = sqlite3.connect(DB, isolation_level=None)
    print(f"=== match_partenaires — {'EXÉCUTION' if APPLY else 'SIMULATION (dry-run)'} ===\n")

    # Index : (sexe, nom_normalisé) -> set d'id_fft  ;  et nom_normalisé -> set (tous sexes)
    by_sexe_name = defaultdict(set)
    by_name = defaultdict(set)
    n_j = 0
    for idf, prenom, nom, sexe in c.execute("SELECT id_fft, prenom, nom, sexe FROM joueurs"):
        key = norm(f"{prenom} {nom}")
        if not key:
            continue
        by_sexe_name[(sexe, key)].add(idf)
        by_name[key].add(idf)
        n_j += 1
    print(f"Index construit : {n_j:,} joueurs\n")

    # Participations à résoudre (partenaire_id vide, partenaire_nom présent) + sexe du joueur + type
    rows = c.execute("""
        SELECT p.id, p.partenaire_nom, p.type, j.sexe
        FROM participations p JOIN joueurs j ON j.id_fft = p.id_joueur
        WHERE (p.partenaire_id IS NULL OR p.partenaire_id='')
          AND p.partenaire_nom IS NOT NULL AND p.partenaire_nom!=''""").fetchall()

    matched, ambigu, absent = 0, 0, 0
    updates = []
    ex_ambigu, ex_absent = [], []
    for pid, pnom, typ, sexe_j in rows:
        key = norm(pnom)
        ps = partenaire_sexe(typ, sexe_j)
        cands = by_sexe_name.get((ps, key)) if ps else by_name.get(key)
        if not cands:
            # fallback : si pool précis vide, tente tous sexes
            cands = by_name.get(key)
        if not cands:
            absent += 1
            if len(ex_absent) < 5: ex_absent.append(pnom)
        elif len(cands) == 1:
            matched += 1
            updates.append((next(iter(cands)), pid))
        else:
            ambigu += 1
            if len(ex_ambigu) < 5: ex_ambigu.append(f"{pnom} ({len(cands)} candidats)")

    tot = len(rows)
    print(f"Participations à résoudre : {tot:,}")
    print(f"  ✅ matchés (unique)      : {matched:,}  ({100*matched/max(tot,1):.1f}%)")
    print(f"  ⚠️  ambigus (homonymes)  : {ambigu:,}  ({100*ambigu/max(tot,1):.1f}%)")
    print(f"  ❌ introuvables          : {absent:,}  ({100*absent/max(tot,1):.1f}%)")
    if ex_ambigu: print(f"\n  ex. ambigus    : {ex_ambigu}")
    if ex_absent: print(f"  ex. introuvables: {ex_absent}")

    if not APPLY:
        print("\n(dry-run) Rien écrit. Relance avec --apply pour enregistrer les partenaire_id.")
        return

    print(f"\n⏳ Écriture de {len(updates):,} partenaire_id…")
    c.execute("BEGIN")
    c.executemany("UPDATE participations SET partenaire_id=? WHERE id=?", updates)
    c.execute("COMMIT")
    c.execute("CREATE INDEX IF NOT EXISTS idx_part_partenaire_id ON participations(partenaire_id)")
    print("  ✓ terminé.")


if __name__ == '__main__':
    main()
