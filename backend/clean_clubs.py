"""
clean_clubs.py — Retire de la table `clubs` les entrées parasites dont le NOM est en fait
une ville (ex. « LES LILAS » au lieu d'un vrai club « TC LES LILAS »).

Ces entrées viennent de `joueurs.club_nom` mal renseignés (un joueur dont le club est juste
le nom de sa ville). Elles polluent la carte, la page /clubs et les "meilleurs clubs".

Méthode : un club est parasite si son nom normalisé est EXACTEMENT un nom de commune connu
(sources : villes_geo, stats_geo_ville, joueurs.ville). On garde « TC LES LILAS » (≠ « LES LILAS »).

Usage :
    python clean_clubs.py            # dry-run : liste les clubs parasites détectés
    python clean_clubs.py --apply    # supprime réellement ces clubs
"""
import sqlite3, os, sys, re, unicodedata

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tenup.db')
APPLY = '--apply' in sys.argv


def norm(s: str) -> str:
    s = unicodedata.normalize('NFKD', s or '').encode('ascii', 'ignore').decode().upper()
    s = re.sub(r'[^A-Z0-9]', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def main():
    c = sqlite3.connect(DB, isolation_level=None)

    # Ensemble des noms de communes connus (plusieurs sources, robustesse)
    villes = set()
    for q in (
        "SELECT DISTINCT ville FROM villes_geo",
        "SELECT DISTINCT ville FROM stats_geo_ville",
        "SELECT DISTINCT ville FROM joueurs WHERE ville IS NOT NULL AND ville != ''",
    ):
        try:
            for (v,) in c.execute(q):
                n = norm(v)
                if n:
                    villes.add(n)
        except sqlite3.OperationalError:
            pass
    print(f"{len(villes):,} noms de communes connus.")

    clubs = c.execute("SELECT id, nom FROM clubs WHERE nom IS NOT NULL AND nom != ''").fetchall()
    junk = [(cid, nom) for cid, nom in clubs if norm(nom) in villes]

    print(f"\n⚠️  {len(junk)} clubs parasites détectés (nom = une ville) sur {len(clubs):,} clubs.")
    for cid, nom in junk[:30]:
        print(f"   #{cid}  {nom}")
    if len(junk) > 30:
        print(f"   … (+{len(junk) - 30} autres)")

    if not junk:
        print("\nRien à nettoyer."); return

    if APPLY:
        ids = [cid for cid, _ in junk]
        ph = ",".join("?" * len(ids))
        c.execute(f"DELETE FROM clubs WHERE id IN ({ph})", ids)
        # nettoie aussi les rattachements tournoi→club devenus orphelins (si la table existe)
        try:
            c.execute(f"DELETE FROM tournois_club WHERE club_id IN ({ph})", ids)
        except sqlite3.OperationalError:
            pass
        print(f"\n🗑️  {len(ids)} clubs parasites supprimés.")
    else:
        print("\n(dry-run) Relance avec --apply pour supprimer.")


if __name__ == '__main__':
    main()
