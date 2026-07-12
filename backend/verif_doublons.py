import sqlite3, os
c = sqlite3.connect(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tenup.db'))
tot = c.execute("SELECT COUNT(*) FROM participations").fetchone()[0]
print(f"participations en base : {tot:,}")
todel = c.execute("""SELECT COUNT(*) FROM participations p WHERE length(p.id_tournoi)<8
  AND EXISTS(SELECT 1 FROM participations d WHERE d.id_joueur=p.id_joueur AND length(d.id_tournoi)>=8)""").fetchone()[0]
print("calcul en cours (30-60s)...")
lost = c.execute("""SELECT COUNT(*) FROM participations p JOIN tournois tp ON tp.id_tournoi=p.id_tournoi
  WHERE length(p.id_tournoi)<8
    AND EXISTS(SELECT 1 FROM participations d WHERE d.id_joueur=p.id_joueur AND length(d.id_tournoi)>=8)
    AND NOT EXISTS(SELECT 1 FROM participations d JOIN tournois td ON td.id_tournoi=d.id_tournoi
                   WHERE d.id_joueur=p.id_joueur AND length(d.id_tournoi)>=8 AND td.nom=tp.nom)""").fetchone()[0]
print(f"\na supprimer (ancien systeme, joueur re-scrape) : {todel:,}")
print(f"  -> nom deja present dans le nouveau (redondant SUR) : {todel-lost:,}  ({100*(todel-lost)/todel:.1f}%)")
print(f"  -> nom ABSENT du nouveau (vieille edition, a arbitrer): {lost:,}  ({100*lost/todel:.1f}%)")
