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
OUT_EVO = os.path.join(HERE, "..", "frontend", "dashboard", "evolution.json")


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
    # ── Nouveaux classés du mois (1re apparition au dernier mois) ────────────
    evo = {}
    try:
        latest = months[-1]
        prev = months[-2] if len(months) >= 2 else None
        con.execute("CREATE TEMP TABLE _newp AS SELECT id_joueur FROM classements_historique GROUP BY id_joueur HAVING MIN(mois)=?", (latest,))
        tot_new = con.execute("SELECT COUNT(*) FROM _newp").fetchone()[0]
        sx = dict(con.execute("SELECT j.sexe, COUNT(*) FROM _newp JOIN joueurs j ON j.id_fft=_newp.id_joueur GROUP BY j.sexe").fetchall())
        _rows = con.execute("SELECT j.dept_num, MAX(j.comite) AS nom, COUNT(*) AS n FROM _newp JOIN joueurs j ON j.id_fft=_newp.id_joueur WHERE j.dept_num IS NOT NULL AND j.dept_num!='' GROUP BY j.dept_num").fetchall()
        by_dept = {str(d): int(n) for d, nom, n in _rows}
        dept_names = {str(d): (nom or "") for d, nom, n in _rows}
        evo = {"updated": datetime.date.today().isoformat(), "mois": latest, "prev": prev,
               "total": int(tot_new), "h": int(sx.get("H", 0)), "f": int(sx.get("F", 0)),
               "by_dept": by_dept, "dept_names": dept_names}
    except Exception as e:
        print("evolution: skip (", e, ")")
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
    if evo:
        with open(OUT_EVO, "w", encoding="utf-8") as f:
            json.dump(evo, f, ensure_ascii=False)
        print(f"evolution.json : {evo['total']:,} nouveaux classés en {evo['mois']} ({len(evo['by_dept'])} départements)")


if __name__ == "__main__":
    main()
