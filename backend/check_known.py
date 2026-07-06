"""check_known.py — statut précis des 3 profils témoins. Lance : python check_known.py"""
import sqlite3
c = sqlite3.connect('tenup.db')
for idf in ['7633273415', '1953828852', '1329333964']:
    j  = c.execute("SELECT nom,prenom,classement,points,dernier_mois_vu,scraped_at FROM joueurs WHERE id_fft=?", (idf,)).fetchone()
    qs = c.execute("SELECT statut,scraped_at,retries,error FROM scrape_queue WHERE id_fft=?", (idf,)).fetchone()
    np = c.execute("SELECT COUNT(*) FROM participations WHERE id_joueur=?", (idf,)).fetchone()[0]
    # une participation récente (points_num renseigné = écrite par le nouveau scraper JSON)
    neuf = c.execute("SELECT COUNT(*) FROM participations WHERE id_joueur=? AND points_num IS NOT NULL", (idf,)).fetchone()[0]
    print(f"\n{idf} — {j[1]} {j[0]}")
    print(f"  joueurs : classement={j[2]} points={j[3]} dernier_mois_vu={j[4]} scraped_at={j[5]}")
    print(f"  queue   : {qs}")
    print(f"  participations : {np} (dont {neuf} écrites par le scraper JSON / points_num renseigné)")
