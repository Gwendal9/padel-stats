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

print(f"\nTotal à supprimer : {orphan + stale:,}")
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
