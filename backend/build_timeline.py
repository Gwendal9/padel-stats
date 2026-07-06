"""Précalcule les séries temporelles pour la courbe "explosion du padel".

Écrit frontend/dashboard/timeline.json :
  - months        : ["2018-10", ..., "2026-06"]
  - licencies_h   : nb de joueurs classés (H) chaque mois
  - licencies_f   : nb de joueuses classées (F) chaque mois
  - total         : H + F
  - tournois      : nb de tournois distincts / mois (limité aux ~12 derniers mois,
                    les participations expirées étant purgées) ; null sinon

À lancer après un scrape mensuel (inclus dans run_monthly_full.ps1).
"""
import os
import sqlite3
import json
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "tenup.db")
OUT = os.path.join(HERE, "..", "frontend", "dashboard", "timeline.json")


def main():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = con.execute(
        """SELECT h.mois, j.sexe, COUNT(DISTINCT h.id_joueur) n
           FROM classements_historique h JOIN joueurs j ON j.id_fft = h.id_joueur
           GROUP BY h.mois, j.sexe"""
    ).fetchall()
    months = sorted({m for m, _, _ in rows})
    H = {m: 0 for m in months}
    F = {m: 0 for m in months}
    for m, s, n in rows:
        (H if s == "H" else F)[m] = n

    tr = dict(
        con.execute(
            """SELECT substr(date_tournoi,7,4)||'-'||substr(date_tournoi,4,2) mois,
                      COUNT(DISTINCT id_tournoi) nt
               FROM participations WHERE length(date_tournoi) >= 10
               GROUP BY mois"""
        ).fetchall()
    )
    con.close()

    out = {
        "updated": datetime.date.today().isoformat(),
        "months": months,
        "licencies_h": [H[m] for m in months],
        "licencies_f": [F[m] for m in months],
        "total": [H[m] + F[m] for m in months],
        "tournois": [tr.get(m) for m in months],
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print(f"timeline.json : {len(months)} mois ({months[0]} -> {months[-1]}), "
          f"total final {out['total'][-1]:,}")


if __name__ == "__main__":
    main()
