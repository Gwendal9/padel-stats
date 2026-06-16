"""
match_tournois_clubs.py — Déduit le club organisateur d'un tournoi depuis son NOM.

La base FFT ne stocke pas le club hôte d'un tournoi. On le déduit heuristiquement en
matchant le nom du tournoi contre la liste des clubs connus (`clubs.nom`).

Méthode :
  - normalisation (majuscules, sans accents, sans ponctuation) ;
  - on retire les mots génériques (PADEL, CLUB, P1000, HOMMES, MIXTE, OPEN…) → tokens "distinctifs" ;
  - index inversé token → clubs ; pour chaque tournoi on ne score que les clubs candidats ;
  - match retenu si TOUS les tokens distinctifs du club sont présents dans le nom du tournoi
    (et le club a un signal suffisant : ≥2 tokens, ou 1 token long ≥5) ;
  - en cas d'égalité : le club le plus "spécifique" (plus de tokens, nom le plus long).

Crée la table `tournois_club(id_tournoi, club_id, club_nom, nb_tokens, score)`.
À lancer sous Windows (écrit la base). N'assigne RIEN quand le nom ne contient pas de club
identifiable (ex. "P100 Hommes") → couverture partielle, c'est normal.

Usage :
    python match_tournois_clubs.py            # construit la table
    python match_tournois_clubs.py --show     # + taux de match et exemples
"""
import sqlite3, os, sys, re, unicodedata
from collections import defaultdict

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tenup.db')
SHOW = '--show' in sys.argv

# Mots génériques à ignorer (ne distinguent pas un club)
STOP = {
    'PADEL', 'CLUB', 'TENNIS', 'TC', 'AS', 'ASL', 'US', 'ASC', 'CS', 'SC',
    'LE', 'LA', 'LES', 'DU', 'DE', 'DES', 'D', 'L', 'ET', 'AU', 'AUX', 'AND', 'AND',
    'HOMME', 'HOMMES', 'DAME', 'DAMES', 'MIXTE', 'MESSIEURS', 'FEMININ', 'FEMININE',
    'OPEN', 'TOURNOI', 'JOURNEE', 'JOURNEES', 'SOIREE', 'NUIT', 'BY', 'SPECIAL',
    'GALAXIE', 'SENIOR', 'SENIORS', 'JEUNE', 'JEUNES', 'CHAMPIONNAT', 'CHAMPIONNATS',
    'EQUIPE', 'EQUIPES', 'INTERCLUB', 'INTERCLUBS', 'FFT', 'COMPLEXE', 'CENTRE',
}
_NONALNUM = re.compile(r'[^A-Z0-9 ]')
_PLEVEL   = re.compile(r'^P\d{2,4}$')


def norm(s: str) -> str:
    s = unicodedata.normalize('NFKD', s or '').encode('ascii', 'ignore').decode().upper()
    s = _NONALNUM.sub(' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def distinct_tokens(s: str) -> list:
    out = []
    for t in norm(s).split():
        if t in STOP or _PLEVEL.match(t) or len(t) < 3 or t.isdigit():
            continue
        out.append(t)
    return out


def main():
    c = sqlite3.connect(DB, isolation_level=None)

    clubs = c.execute("SELECT id, nom, ville, dept_num FROM clubs WHERE nom IS NOT NULL AND nom != ''").fetchall()
    club_toks = {}            # club_id -> set(tokens distinctifs)
    club_nom  = {}
    index = defaultdict(set)  # token -> {club_id}
    for cid, nom, ville, dept in clubs:
        toks = set(distinct_tokens(nom))
        if not toks:
            continue
        club_toks[cid] = toks
        club_nom[cid]  = nom
        for t in toks:
            index[t].add(cid)
    print(f"{len(club_toks):,} clubs indexés.")

    def signal_ok(toks: set) -> bool:
        # éviter les matches sur un seul token court/ambigu
        if len(toks) >= 2:
            return True
        t = next(iter(toks))
        return len(t) >= 5

    c.execute("DROP TABLE IF EXISTS tournois_club")
    c.execute('''CREATE TABLE tournois_club (
        id_tournoi TEXT PRIMARY KEY,
        club_id    INTEGER,
        club_nom   TEXT,
        nb_tokens  INTEGER,
        score      INTEGER
    )''')
    c.execute("CREATE INDEX idx_tclub_club ON tournois_club(club_id)")

    rows = c.execute("SELECT id_tournoi, nom FROM tournois").fetchall()
    out, n_match = [], 0
    for tid, nom in rows:
        ttoks = set(distinct_tokens(nom))
        if not ttoks:
            continue
        cand = set()
        for t in ttoks:
            cand |= index.get(t, set())
        best = None
        for cid in cand:
            ctoks = club_toks[cid]
            if not ctoks.issubset(ttoks):     # tous les tokens du club présents dans le tournoi
                continue
            if not signal_ok(ctoks):
                continue
            score = sum(len(t) for t in ctoks)
            cand_key = (len(ctoks), score, len(club_nom[cid]))
            if best is None or cand_key > best[0]:
                best = (cand_key, cid)
        if best:
            cid = best[1]
            out.append((tid, cid, club_nom[cid], len(club_toks[cid]), best[0][1]))
            n_match += 1

    c.executemany("INSERT INTO tournois_club VALUES (?,?,?,?,?)", out)
    print(f"✅ {n_match:,} / {len(rows):,} tournois rattachés à un club "
          f"({100*n_match/max(1,len(rows)):.0f}%).")

    if SHOW:
        print("\nExemples de rattachements :")
        for tid, club, nom in c.execute('''
            SELECT tc.id_tournoi, tc.club_nom, t.nom
            FROM tournois_club tc JOIN tournois t ON t.id_tournoi = tc.id_tournoi
            ORDER BY RANDOM() LIMIT 12'''):
            print(f"  « {(nom or '')[:46]:46} » → {club}")
        print("\nClubs avec le plus de tournois rattachés :")
        for club, n in c.execute('''
            SELECT club_nom, COUNT(*) n FROM tournois_club
            GROUP BY club_id ORDER BY n DESC LIMIT 10'''):
            print(f"  {n:>4}  {club}")


if __name__ == '__main__':
    main()
