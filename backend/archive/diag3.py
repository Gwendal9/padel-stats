"""diag3.py — test de persistance réel sur tenup.db. Lance : python diag3.py"""
import sqlite3, os

DB = 'tenup.db'
print("Taille tenup.db :", os.path.getsize(DB), "octets")
print("Présence WAL :", os.path.exists(DB + '-wal'), "| SHM :", os.path.exists(DB + '-shm'))

# --- État actuel ---
c = sqlite3.connect(DB, isolation_level=None)
c.execute("PRAGMA journal_mode=WAL")
print("\njoueurs total :", c.execute("SELECT COUNT(*) FROM joueurs").fetchone()[0])
print("dernier_mois_vu='2026-06' :", c.execute("SELECT COUNT(*) FROM joueurs WHERE dernier_mois_vu='2026-06'").fetchone()[0])
print("scrape_queue pending :", c.execute("SELECT COUNT(*) FROM scrape_queue WHERE statut='pending'").fetchone()[0])
# un joueur frais connu
print("Thomas Leygue (1329333964) :",
      c.execute("SELECT dernier_mois_vu, points FROM joueurs WHERE id_fft='1329333964'").fetchone())

# --- TEST écriture/relecture ---
print("\n=== test persistance ===")
c.execute("INSERT INTO joueurs (id_fft, nom, dernier_mois_vu) VALUES ('ZZTEST','SENTINELLE','2026-06') "
          "ON CONFLICT(id_fft) DO UPDATE SET dernier_mois_vu='2026-06', nom='SENTINELLE'")
c.commit()
print("écrit ZZTEST, lu dans la même connexion :",
      c.execute("SELECT id_fft, dernier_mois_vu FROM joueurs WHERE id_fft='ZZTEST'").fetchone())
c.close()

# rouvre une NOUVELLE connexion
c2 = sqlite3.connect(DB)
r = c2.execute("SELECT id_fft, dernier_mois_vu FROM joueurs WHERE id_fft='ZZTEST'").fetchone()
print("relu après fermeture/réouverture :", r)
print("=> Si c'est None ici, les écritures ne persistent PAS (problème disque/WAL).")
# nettoyage
c2.close()
c3 = sqlite3.connect(DB, isolation_level=None)
c3.execute("DELETE FROM joueurs WHERE id_fft='ZZTEST'")
c3.close()
