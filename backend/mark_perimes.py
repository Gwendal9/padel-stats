"""
Marque les joueurs au « classement périmé ».

Quand un joueur n'apparaît plus dans la liste FFT du mois (ses points ont expiré
après 12 mois, licence non renouvelée...), le scraper ne le touche plus : il garde
son ancien rang dans joueurs.classement. Résultat : les compteurs, classements,
cartes et moyennes comptaient des joueurs qui ne sont plus classés.

Ce script (idempotent, à lancer APRÈS chaque scrape et AVANT build_geo / build_timeline) :
  1. ajoute la colonne joueurs.classement_perime (si absente)
  2. pour chaque joueur dont classement_date < dernier mois scrapé :
       classement_perime <- classement ; classement <- NULL
     (classement_date reste le dernier mois où il était classé)
  3. pour chaque joueur à nouveau classé ce mois-ci : classement_perime <- NULL

Les classements_historique (trajectoire) ne sont pas touchés. meilleur_classement non plus.

Usage : python mark_perimes.py [--db chemin/vers/tenup.db] [--dry-run]
"""
import argparse
import os
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(HERE, "tenup.db"))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    c = sqlite3.connect(a.db)
    cols = [r[1] for r in c.execute("PRAGMA table_info(joueurs)")]
    if "classement_perime" not in cols:
        c.execute("ALTER TABLE joueurs ADD COLUMN classement_perime INTEGER")
        print("colonne classement_perime ajoutée")

    cur = c.execute("SELECT MAX(classement_date) FROM joueurs WHERE classement_date IS NOT NULL").fetchone()[0]
    if not cur:
        print("Aucun classement_date : rien à faire.")
        return
    n_cur = c.execute("SELECT COUNT(*) FROM joueurs WHERE classement IS NOT NULL AND classement_date=?", (cur,)).fetchone()[0]
    # classement_date NULL = anciennes lignes (scrape d'avril) jamais revues dans une liste mensuelle
    n_stale = c.execute("SELECT COUNT(*) FROM joueurs WHERE classement IS NOT NULL AND (classement_date IS NULL OR classement_date<?)", (cur,)).fetchone()[0]
    n_back = c.execute("SELECT COUNT(*) FROM joueurs WHERE classement_perime IS NOT NULL AND classement_date=?", (cur,)).fetchone()[0]
    print(f"Dernier mois : {cur} | classés ce mois : {n_cur:,} | à périmer : {n_stale:,} | de retour : {n_back:,}")

    # garde-fou : si le scrape du mois est manifestement incomplet, on n'efface rien
    if n_cur < 100000:
        print("⚠ Moins de 100 000 classés sur le dernier mois : scrape incomplet ? Abandon, rien modifié.")
        return
    if a.dry_run:
        print("dry-run : rien modifié.")
        return

    c.execute("UPDATE joueurs SET classement_perime=classement, classement=NULL "
              "WHERE classement IS NOT NULL AND (classement_date IS NULL OR classement_date<?)", (cur,))
    c.execute("UPDATE joueurs SET classement_perime=NULL "
              "WHERE classement_perime IS NOT NULL AND classement_date=?", (cur,))
    c.execute("CREATE INDEX IF NOT EXISTS idx_joueurs_perime ON joueurs(classement_perime)")
    c.commit()

    r = c.execute("SELECT sexe, COUNT(*) FROM joueurs WHERE classement IS NOT NULL GROUP BY sexe").fetchall()
    p = c.execute("SELECT sexe, COUNT(*) FROM joueurs WHERE classement_perime IS NOT NULL GROUP BY sexe").fetchall()
    print("Classés :", r, "| Périmés :", p)
    c.close()


if __name__ == "__main__":
    main()
