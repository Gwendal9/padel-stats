"""
cleanup_db.py — Nettoyage de la base après un scrape mensuel.

Supprime :
  1. les participations orphelines (id_joueur absent de la table joueurs)
  2. les vieilles participations non rafraîchies ce cycle : pour les joueurs vus
     dans la liste de ce mois, les participations écrites par l'ANCIEN scraper HTML
     (reconnaissables car pris_en_compte IS NULL — le scraper JSON met toujours 0 ou 1).

⚠️ DRY-RUN par défaut : affiche ce qui SERAIT supprimé, sans rien toucher.
   Ajoute --apply pour exécuter réellement (fais un backup avant).

Usage :
    python cleanup_db.py            # simulation
    python cleanup_db.py --apply    # exécution réelle
    python cleanup_db.py --apply --vacuum   # + compacte le fichier
"""
import sqlite3, sys, os
from datetime import datetime

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tenup.db')
APPLY = '--apply' in sys.argv
VACUUM = '--vacuum' in sys.argv
MOIS = datetime.now().strftime('%Y-%m')

c = sqlite3.connect(DB, isolation_level=None)

def n(sql, *a): return c.execute(sql, a).fetchone()[0]

print(f"=== cleanup_db — {'EXÉCUTION' if APPLY else 'SIMULATION (dry-run)'} | mois={MOIS} ===\n")

# 1) participations orphelines
orphan = n("SELECT COUNT(*) FROM participations p WHERE NOT EXISTS "
           "(SELECT 1 FROM joueurs j WHERE j.id_fft=p.id_joueur)")
print(f"1) Participations orphelines (joueur inexistant)        : {orphan:,}")

# 2) vieilles participations non rafraîchies (joueurs vus ce mois, pris_en_compte NULL)
stale = n("""SELECT COUNT(*) FROM participations p
             WHERE p.pris_en_compte IS NULL
               AND EXISTS (SELECT 1 FROM joueurs j WHERE j.id_fft=p.id_joueur
                           AND j.dernier_mois_vu=? AND j.points IS NOT NULL)""", MOIS)
print(f"2) Vieilles participations non rafraîchies (joueurs {MOIS}) : {stale:,}")

# 3) artefacts "None None" (partenaire anonyme mal stocké) -> à vider
nonenone = n("SELECT COUNT(*) FROM participations WHERE partenaire_nom IN ('None None','None','none none')")
print(f"3) partenaire_nom = 'None None' (anonymes, à vider) : {nonenone:,}")

# 4) doublons de la transition ancien->nouveau scraper : participation ANCIENNE (id 6 ch.)
#    dont le MÊME tournoi (par nom) existe aussi en NOUVEAU chez ce joueur.
#    On GARDE les vieilles perfs dont le tournoi n'est plus au bilan (historique).
dup = n("""SELECT COUNT(*) FROM participations p JOIN tournois tp ON tp.id_tournoi=p.id_tournoi
           WHERE length(p.id_tournoi) < 8
             AND EXISTS (SELECT 1 FROM participations d JOIN tournois td ON td.id_tournoi=d.id_tournoi
                         WHERE d.id_joueur=p.id_joueur AND length(d.id_tournoi) >= 8 AND td.nom=tp.nom
                           AND d.points_num IS p.points_num AND d.position_num IS p.position_num)""")
print(f"4) Doublons ancien systeme (meme tournoi + resultat) : {dup:,}")

print(f"\nTotal à supprimer : {orphan + stale + dup:,}")
print(f"Participations avant : {n('SELECT COUNT(*) FROM participations'):,}")

if not APPLY:
    print("\n(dry-run) Rien supprimé. Relance avec --apply pour exécuter.")
    sys.exit(0)

print("\n⏳ Suppression…")
d1 = c.execute("DELETE FROM participations WHERE NOT EXISTS "
               "(SELECT 1 FROM joueurs j WHERE j.id_fft=participations.id_joueur)").rowcount
d2 = c.execute("""DELETE FROM participations
                  WHERE pris_en_compte IS NULL
                    AND id_joueur IN (SELECT id_fft FROM joueurs
                                      WHERE dernier_mois_vu=? AND points IS NOT NULL)""", (MOIS,)).rowcount
print(f"  orphelines supprimées : {d1:,}")
print(f"  vieilles supprimées   : {d2:,}")
c.execute("""CREATE TEMP TABLE _newkeys AS SELECT DISTINCT p.id_joueur AS jid, t.nom AS nom, p.points_num AS pts, p.position_num AS pos
             FROM participations p JOIN tournois t ON t.id_tournoi=p.id_tournoi WHERE length(p.id_tournoi) >= 8""")
c.execute("CREATE INDEX _idx_nk ON _newkeys(jid, nom)")
d4 = c.execute("""DELETE FROM participations WHERE id IN (
                    SELECT p.id FROM participations p JOIN tournois t ON t.id_tournoi=p.id_tournoi
                    WHERE length(p.id_tournoi) < 8
                      AND EXISTS(SELECT 1 FROM _newkeys n WHERE n.jid=p.id_joueur AND n.nom=t.nom
                                 AND n.pts IS p.points_num AND n.pos IS p.position_num))""").rowcount
c.execute("DROP TABLE _newkeys")
print(f"  doublons ancien systeme (meme tournoi + resultat) supprimes : {d4:,}")
print(f"Participations après    : {n('SELECT COUNT(*) FROM participations'):,}")

# normalise les "None None" (partenaire anonyme) -> chaîne vide
d3 = c.execute("UPDATE participations SET partenaire_nom='' "
               "WHERE partenaire_nom IN ('None None','None','none none')").rowcount
print(f"  partenaire 'None None' vidés : {d3:,}")

# tournois devenus inutiles (plus aucune participation)
dt = c.execute("DELETE FROM tournois WHERE NOT EXISTS "
               "(SELECT 1 FROM participations p WHERE p.id_tournoi=tournois.id_tournoi)").rowcount
print(f"Tournois orphelins supprimés : {dt:,}")

if VACUUM:
    print("\n⏳ VACUUM (compactage du fichier)…")
    c.execute("VACUUM")
    print("  ✓ fichier compacté")

print("\n✓ Nettoyage terminé.")
