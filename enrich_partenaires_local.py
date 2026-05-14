"""
enrich_partenaires_local.py
───────────────────────────
Enrichissement local des partenaire_id manquants.

Principe : pour chaque partenaire_nom unique sans partenaire_id,
on cherche dans la table joueurs une correspondance exacte sur
(prenom + nom) ou (nom + prenom).

On ne valide le match QUE si :
  - exactement 1 joueur correspond (pas d'ambiguïté)
  - les deux ordres de lecture (si différents) donnent le même joueur OU
    un seul des deux ordres donne exactement 1 résultat

Usage :
    python enrich_partenaires_local.py            # dry-run (affiche les stats)
    python enrich_partenaires_local.py --apply    # applique les changements
    python enrich_partenaires_local.py --examples # affiche des exemples par catégorie
    python enrich_partenaires_local.py --apply --batch 5000  # commit tous les N updates
"""

import argparse
import sqlite3
import os
from collections import defaultdict

BASE_DIR = os.path.dirname(__file__)
DB_FILE  = os.path.join(BASE_DIR, 'tenup.db')

ANON_NAMES = {"joueur anonyme", "anonyme", "joueur anon", "inconnu"}


def normalize(s: str) -> str:
    return s.strip().upper() if s else ""


def is_anonymous(name: str) -> bool:
    return normalize(name).lower() in ANON_NAMES


def build_joueurs_index(conn):
    """
    Construit deux index :
      - prenom_nom_index : (PRENOM_NOM) → [id_fft, ...]
      - nom_prenom_index : (NOM_PRENOM) → [id_fft, ...]
    """
    rows = conn.execute("SELECT id_fft, nom, prenom FROM joueurs WHERE nom IS NOT NULL").fetchall()

    pn_index  = defaultdict(list)   # "PRENOM NOM" -> [id_fft]
    np_index  = defaultdict(list)   # "NOM PRENOM" -> [id_fft]

    for id_fft, nom, prenom in rows:
        nom    = normalize(nom)
        prenom = normalize(prenom) if prenom else ""
        if nom:
            key_pn = f"{prenom} {nom}".strip() if prenom else nom
            key_np = f"{nom} {prenom}".strip() if prenom else nom
            pn_index[key_pn].append(id_fft)
            if key_pn != key_np:
                np_index[key_np].append(id_fft)

    return pn_index, np_index


def resolve_name(partenaire_nom: str, pn_index: dict, np_index: dict):
    """
    Essaie de résoudre partenaire_nom vers un id_fft unique.
    Retourne (id_fft, méthode) ou (None, raison).
    """
    key = normalize(partenaire_nom)

    hits_pn = pn_index.get(key, [])
    hits_np = np_index.get(key, [])

    # Combiner toutes les correspondances (les deux index peuvent se chevaucher)
    all_hits = set(hits_pn) | set(hits_np)

    if len(all_hits) == 1:
        return list(all_hits)[0], "unique"
    elif len(all_hits) == 0:
        return None, "no_match"
    else:
        return None, "ambiguous"


def main():
    parser = argparse.ArgumentParser(description='Enrichissement local partenaire_id')
    parser.add_argument('--apply',    action='store_true', help='Applique les changements (sinon dry-run)')
    parser.add_argument('--examples', action='store_true', help='Affiche des exemples par catégorie')
    parser.add_argument('--batch',    type=int, default=5000, help='Taille des commits (défaut: 5000)')
    parser.add_argument('--limit',    type=int, default=0,    help='Limiter le nb de noms traités (debug)')
    args = parser.parse_args()

    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA journal_mode=WAL")

    # ── Stats globales ────────────────────────────────────────────────
    total_rows = conn.execute(
        "SELECT COUNT(*) FROM participations WHERE (partenaire_id IS NULL OR partenaire_id='') AND partenaire_nom IS NOT NULL AND partenaire_nom != ''"
    ).fetchone()[0]
    print(f"📊 Participations sans partenaire_id : {total_rows:,}")

    # Noms uniques à résoudre (hors anonymes)
    print("🔍 Chargement des noms uniques...")
    query = """
        SELECT DISTINCT partenaire_nom
        FROM participations
        WHERE (partenaire_id IS NULL OR partenaire_id = '')
          AND partenaire_nom IS NOT NULL
          AND partenaire_nom != ''
        ORDER BY partenaire_nom
    """
    if args.limit > 0:
        query += f" LIMIT {args.limit}"
    unique_names = [r[0] for r in conn.execute(query).fetchall()]

    # Filtrer les anonymes
    names_to_resolve = [n for n in unique_names if not is_anonymous(n)]
    skipped_anon     = len(unique_names) - len(names_to_resolve)
    print(f"   {len(unique_names):,} noms uniques · {skipped_anon:,} anonymes ignorés → {len(names_to_resolve):,} à résoudre")

    # ── Construction des index joueurs ────────────────────────────────
    print("🗂️  Construction des index joueurs...")
    pn_index, np_index = build_joueurs_index(conn)
    nb_joueurs = conn.execute("SELECT COUNT(*) FROM joueurs").fetchone()[0]
    print(f"   {nb_joueurs:,} joueurs indexés")

    # ── Résolution ────────────────────────────────────────────────────
    print("⚙️  Résolution en cours...")
    results = {
        "unique":    [],   # (partenaire_nom, id_fft)
        "ambiguous": [],   # partenaire_nom
        "no_match":  [],   # partenaire_nom
    }

    for name in names_to_resolve:
        id_fft, reason = resolve_name(name, pn_index, np_index)
        if reason == "unique":
            results["unique"].append((name, id_fft))
        elif reason == "ambiguous":
            results["ambiguous"].append(name)
        else:
            results["no_match"].append(name)

    # ── Rapport ───────────────────────────────────────────────────────
    nb_unique    = len(results["unique"])
    nb_ambiguous = len(results["ambiguous"])
    nb_no_match  = len(results["no_match"])
    nb_total     = len(names_to_resolve)

    print(f"\n{'='*55}")
    print(f"📊 Résultats de la résolution locale :")
    print(f"   Noms traités      : {nb_total:,}")
    print(f"   ✅ Match unique   : {nb_unique:,}  ({nb_unique/nb_total*100:.1f}%)")
    print(f"   ⚠️  Ambigus        : {nb_ambiguous:,}  ({nb_ambiguous/nb_total*100:.1f}%)")
    print(f"   ❓ Aucun résultat  : {nb_no_match:,}  ({nb_no_match/nb_total*100:.1f}%)")

    # Estimer le nb de lignes impactées
    if nb_unique > 0:
        resolved_names_set = {name for name, _ in results["unique"]}
        # Count rows that would be updated
        # (can't do IN with 100k names efficiently, so estimate by ratio)
        est_rows = int(total_rows * nb_unique / nb_total)
        print(f"\n   Lignes impactées estimées : ~{est_rows:,} / {total_rows:,} ({est_rows/total_rows*100:.1f}%)")

    # ── Exemples ──────────────────────────────────────────────────────
    if args.examples:
        print(f"\n── 15 exemples de matchs UNIQUES ──────────────────────")
        for name, id_fft in results["unique"][:15]:
            joueur = conn.execute(
                "SELECT prenom, nom, classement, club_nom FROM joueurs WHERE id_fft=?", (id_fft,)
            ).fetchone()
            if joueur:
                prenom, nom, classement, club = joueur
                print(f"   '{name}' → {prenom} {nom} (cl:{classement}, {club or '?'}) [{id_fft}]")

        print(f"\n── 10 exemples AMBIGUS (plusieurs joueurs possibles) ───")
        for name in results["ambiguous"][:10]:
            key = normalize(name)
            pn_hits = pn_index.get(key, [])
            np_hits = np_index.get(key, [])
            all_ids = list(set(pn_hits) | set(np_hits))[:4]
            candidates = []
            for fft_id in all_ids:
                j = conn.execute("SELECT prenom, nom FROM joueurs WHERE id_fft=?", (fft_id,)).fetchone()
                if j:
                    candidates.append(f"{j[0]} {j[1]} [{fft_id}]")
            print(f"   '{name}' → {len(all_ids)} candidats : {', '.join(candidates)}")

        print(f"\n── 10 exemples SANS match ──────────────────────────────")
        for name in results["no_match"][:10]:
            print(f"   '{name}'")

    # ── Application ───────────────────────────────────────────────────
    if not args.apply:
        print(f"\n⚠️  Mode dry-run — aucune modification effectuée.")
        print(f"    Relancer avec --apply pour appliquer les {nb_unique:,} matchs uniques.")
        conn.close()
        return

    print(f"\n✏️  Application des {nb_unique:,} matchs uniques via table temporaire...")

    # ── Étape 1 : table temporaire nom → id ──────────────────────────
    conn.execute("CREATE TEMP TABLE _name_to_id (partenaire_nom TEXT PRIMARY KEY, id_fft TEXT)")
    conn.executemany(
        "INSERT INTO _name_to_id VALUES (?, ?)",
        results["unique"]
    )
    conn.commit()
    print("   Table temporaire créée.")

    # ── Étape 2 : UPDATE en masse (une seule passe) ───────────────────
    conn.execute("""
        UPDATE participations
        SET partenaire_id = (
            SELECT id_fft FROM _name_to_id
            WHERE _name_to_id.partenaire_nom = participations.partenaire_nom
        )
        WHERE partenaire_nom IN (SELECT partenaire_nom FROM _name_to_id)
          AND (partenaire_id IS NULL OR partenaire_id = '')
    """)
    conn.commit()
    updated = conn.execute("SELECT changes()").fetchone()[0]
    print(f"   {updated:,} lignes mises à jour.")

    # ── Étape 3 : joueurs inconnus → scrape_queue ─────────────────────
    conn.execute("""
        INSERT OR IGNORE INTO scrape_queue (id_fft, statut, added_at)
        SELECT DISTINCT n.id_fft, 'pending', datetime('now')
        FROM _name_to_id n
        WHERE n.id_fft NOT IN (SELECT id_fft FROM joueurs)
          AND n.id_fft NOT IN (SELECT id_fft FROM scrape_queue)
    """)
    conn.commit()
    added_queue = conn.execute("SELECT changes()").fetchone()[0]

    conn.execute("DROP TABLE IF EXISTS _name_to_id")
    conn.commit()
    conn.close()

    print(f"\n{'='*55}")
    print(f"✅ Enrichissement terminé :")
    print(f"   Noms résolus        : {nb_unique:,}")
    print(f"   Lignes mises à jour : {updated:,}")
    print(f"   Nouveaux en queue   : {added_queue:,}")


if __name__ == '__main__':
    main()
