import sqlite3

# Lecture seule — n'interfère pas avec le scraper en cours
conn = sqlite3.connect("file:tenup.db?mode=ro", uri=True)

# ── Trouver les tournois les mieux couverts (max de paires connues) ──
print("=== TOP 10 TOURNOIS LES MIEUX COUVERTS ===\n")
top = conn.execute("""
    SELECT
        p.id_tournoi,
        t.nom,
        t.categorie,
        COUNT(DISTINCT p.id_joueur) as nb_joueurs,
        COUNT(DISTINCT p.id_joueur) / 2 as nb_paires_approx,
        MIN(p.date_tournoi) as date
    FROM participations p
    JOIN tournois t ON t.id_tournoi = p.id_tournoi
    WHERE p.id_tournoi != ''
    GROUP BY p.id_tournoi
    ORDER BY nb_joueurs DESC
    LIMIT 10
""").fetchall()

for r in top:
    print(f"  [{r[4]} paires] {r[1]} ({r[2]}) — {r[5]} — id:{r[0]}")

# ── Reconstituer un tournoi complet ──
tournoi_id = top[0][0]
tournoi_nom = top[0][1]
tournoi_cat = top[0][2]

print(f"\n{'='*60}")
print(f"RECONSTRUCTION : {tournoi_nom} ({tournoi_cat})")
print(f"ID : {tournoi_id}")
print(f"{'='*60}\n")

parts = conn.execute("""
    SELECT
        p.id_joueur,
        j.prenom,
        j.nom,
        j.echelon,
        p.partenaire_id,
        p.partenaire_nom,
        p.position,
        p.points,
        p.type
    FROM participations p
    LEFT JOIN joueurs j ON j.id_fft = p.id_joueur
    WHERE p.id_tournoi = ?
    ORDER BY CAST(p.position AS INTEGER) ASC
""", (tournoi_id,)).fetchall()

print(f"{'POS':<5} {'JOUEUR':<25} {'PARTENAIRE':<25} {'ECH':>5} {'PTS':>6} {'TYPE':<5}")
print("-" * 75)

paires_vues = set()
for p in parts:
    id_j, prenom, nom, echelon, part_id, part_nom, position, points, type_ = p
    # Dédoublonner : on affiche chaque paire une seule fois
    paire_key = tuple(sorted([id_j, part_id or part_nom]))
    if paire_key in paires_vues:
        continue
    paires_vues.add(paire_key)

    joueur_str = f"{prenom or '?'} {nom or '?'}"
    print(f"{position:<5} {joueur_str:<25} {part_nom:<25} {echelon or '?':>5} {points:>6} {type_:<5}")

print(f"\n→ {len(paires_vues)} paires reconstituées")

# ── Vérifier la cohérence : chaque joueur a-t-il le même partenaire ? ──
print(f"\n=== VÉRIFICATION COHÉRENCE (paires croisées) ===")
incoherences = 0
for p in parts:
    id_j, _, _, _, part_id, part_nom, position, _, _ = p
    if not part_id:
        continue
    # Le partenaire devrait avoir la même position dans ce tournoi
    partner_part = conn.execute("""
        SELECT position, partenaire_id FROM participations
        WHERE id_tournoi = ? AND id_joueur = ?
    """, (tournoi_id, part_id)).fetchone()

    if partner_part:
        if partner_part[0] != position:
            print(f"  ⚠️  Positions différentes : joueur {id_j} pos={position}, partenaire {part_id} pos={partner_part[0]}")
            incoherences += 1
    else:
        pass  # partenaire pas encore scrapé, normal

if incoherences == 0:
    print(f"  Tout cohérent ✅")

# ── Stats globales sur la reconstruction ──
print(f"\n=== QUALITÉ GLOBALE DE RECONSTRUCTION ===")
total_parts = conn.execute("SELECT COUNT(*) FROM participations WHERE id_tournoi != ''").fetchone()[0]
avec_partner_id = conn.execute("SELECT COUNT(*) FROM participations WHERE partenaire_id != '' AND partenaire_id IS NOT NULL").fetchone()[0]
print(f"  Participations totales          : {total_parts}")
print(f"  Avec partenaire identifié (id)  : {avec_partner_id} ({100*avec_partner_id//total_parts}%)")
print(f"  Tournois avec 10+ joueurs       : {conn.execute('SELECT COUNT(*) FROM (SELECT id_tournoi, COUNT(*) as c FROM participations GROUP BY id_tournoi HAVING c >= 10)').fetchone()[0]}")
print(f"  Tournois avec 20+ joueurs       : {conn.execute('SELECT COUNT(*) FROM (SELECT id_tournoi, COUNT(*) as c FROM participations GROUP BY id_tournoi HAVING c >= 20)').fetchone()[0]}")

conn.close()
