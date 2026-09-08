"""
precompute.py — Pré-calcule les réponses des endpoints lourds une fois pour toutes.

Stratégie : exécute les routes Flask via test_request_context, sérialise leurs
réponses en JSON, et stocke dans la table `cache_responses` (rempli/mis à jour).
Au runtime, les endpoints lisent juste cette table → réponse instantanée.

USAGE :
  # Sur Render (via Shell tab ou endpoint /api/admin/precompute) :
  DATABASE_URL=postgresql://... python precompute.py

  # En local sur SQLite (pour tester) :
  python precompute.py

Lancer après chaque scrape mensuel des classements FFT.
Durée estimée : 5-10 min sur free tier Render.
"""
import sys
import time
import os

# Permet d'importer api et db depuis ce script standalone
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    # Import tardif : DATABASE_URL doit être set avant d'importer db
    import api  # charge l'app Flask + toutes les routes
    from db import set_cached_body, delete_cached_body, USE_POSTGRES, ensure_indexes, set_statement_timeout

    # Job batch : pas de timeout SQL (les requêtes peuvent prendre plusieurs minutes)
    set_statement_timeout("0")  # 0 = illimité en PostgreSQL

    # S'assurer que les tables (cache_responses, tournois_summary…) existent
    ensure_indexes()

    # ── Étape 0 : matérialiser tournois_summary ─────────────────────────────
    # Ce job fait le gros scan (800k participations) UNE FOIS.
    # Ensuite route_tournaments et route_stats_categories lisent ~3k lignes → <10ms.
    print("⏳ [tournois_summary] construction en cours…", flush=True)
    t0 = time.time()
    try:
        _build_tournois_summary()
        print(f"✅ [tournois_summary] OK en {time.time()-t0:.1f}s", flush=True)
    except Exception as e:
        print(f"❌ [tournois_summary] ÉCHEC après {time.time()-t0:.1f}s : {type(e).__name__}: {e}",
              flush=True)
        import traceback; traceback.print_exc()

    # Liste des (cache_key, fonction_route) à pré-calculer
    JOBS = [
        ("stats",                        api.route_stats),
        ("stats_categories",             api.route_stats_categories),
        ("tournaments_20",               lambda: _call_with_args(api.route_tournaments,    limit="20")),
        ("clubs_100",                    lambda: _call_with_args(api.route_clubs,          top="100")),
        ("clubs_1000",                   lambda: _call_with_args(api.route_clubs,          top="1000")),
        # Classements clubs — 4 tris rapides (pas de JOIN participations)
        ("club_rankings_best_h_200",     lambda: _call_with_args(api.route_club_rankings,  sort="best_h",     top="200")),
        ("club_rankings_best_f_200",     lambda: _call_with_args(api.route_club_rankings,  sort="best_f",     top="200")),
        ("club_rankings_avg_rank_h_200", lambda: _call_with_args(api.route_club_rankings,  sort="avg_rank_h", top="200")),
        ("club_rankings_avg_rank_f_200", lambda: _call_with_args(api.route_club_rankings,  sort="avg_rank_f", top="200")),
        # nb_tournois = JOIN lourd sur participations — en dernier
        ("club_rankings_nb_tournois_200",lambda: _call_with_args(api.route_club_rankings,  sort="nb_tournois",top="200")),
    ]

    print(f"\n🚀 Pré-calcul démarré ({len(JOBS)} jobs) — DATABASE = "
          f"{'PostgreSQL' if USE_POSTGRES else 'SQLite local'}\n", flush=True)
    t_total = time.time()

    for key, fn in JOBS:
        t0 = time.time()
        print(f"⏳ [{key}] calcul en cours…", flush=True)
        try:
            # CRITIQUE : les routes ci-dessus font elles-mêmes un _try_precomputed(key)
            # en tout début de fonction (cache_responses + cache mémoire). Sans ce nettoyage
            # préalable, la route se re-sert son PROPRE ancien cache au lieu de recalculer,
            # et set_cached_body() ci-dessous ne fait que re-timestamper la même donnée
            # figée à l'infini — precompute.py avait donc l'air de tourner (✅ OK) sans
            # jamais rien recalculer depuis la toute première fois. Purge les deux caches
            # (table + mémoire du process) avant d'appeler la route.
            delete_cached_body(key)
            api._MEM_CACHE.pop(key, None)
            with api.app.test_request_context():
                resp = fn()
            # resp peut être Response Flask ou tuple (resp, status)
            if isinstance(resp, tuple):
                resp = resp[0]
            body = resp.get_data(as_text=True)
            set_cached_body(key, body)
            dt = time.time() - t0
            print(f"✅ [{key}] OK en {dt:.1f}s ({len(body)} octets stockés)", flush=True)
        except Exception as e:
            dt = time.time() - t0
            print(f"❌ [{key}] ÉCHEC après {dt:.1f}s : {type(e).__name__}: {e}", flush=True)
            import traceback
            traceback.print_exc()

    print(f"\n🏁 Terminé en {time.time()-t_total:.1f}s total\n", flush=True)


def _build_tournois_summary():
    """
    Peuple la table `tournois_summary` depuis tournois × participations.

    Durée estimée sur Render free tier : 3-8 min (scan ~800k participations, ~3k tournois).
    Après ce job, route_tournaments et route_stats_categories n'ont plus besoin de
    toucher la table participations pour leur cas standard → réponse <10ms.

    Appelée en début de main() avec statement_timeout=0 (pas de kill possible).
    """
    from db import fetchall, USE_POSTGRES, DB_PATH

    # ── 1. Calculer les agrégats (requête lourde — pas de timeout ici) ────────
    print("     → agrégats tournois×participations…", flush=True)
    # date_effective = date_tournoi réelle si connue, sinon estimée (expiration-365j) —
    # la FFT ne fournit plus date_tournoi depuis 03/2026 (cf NOTE_date_tournoi_et_pipeline.md),
    # rempli par backend/backfill_date_effective.py. date_estimee=1 si au moins une des
    # participations du tournoi a une date estimée (en pratique elles le sont toutes ou aucune).
    rows = fetchall("""
        SELECT
          t.id_tournoi,
          t.nom,
          t.categorie,
          MIN(p.date_effective)       AS date_min,
          MAX(p.date_estimee)         AS date_estimee,
          COUNT(DISTINCT p.id_joueur) AS nb_joueurs
        FROM tournois t
        JOIN participations p ON p.id_tournoi = t.id_tournoi
        GROUP BY t.id_tournoi, t.nom, t.categorie
    """)
    print(f"     → {len(rows)} tournois agrégés", flush=True)

    def _to_sort_key(d: str) -> str:
        """DD/MM/YYYY → YYYYMMDD (tri lexicographique = tri chronologique)."""
        if d and len(d) == 10:
            return d[6:10] + d[3:5] + d[0:2]
        return ""

    tuples = [
        (
            r["id_tournoi"],
            r["nom"],
            r["categorie"],
            r["date_min"],
            _to_sort_key(r["date_min"] or ""),
            int(r["date_estimee"]) if r["date_estimee"] is not None else None,
            r["nb_joueurs"],
        )
        for r in rows
    ]

    # ── 2. Écrire dans tournois_summary (UPSERT bulk) ─────────────────────────
    if USE_POSTGRES:
        import psycopg2, psycopg2.extras
        DATABASE_URL = os.environ["DATABASE_URL"]
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = False
        with conn.cursor() as cur:
            # TRUNCATE + bulk INSERT → plus rapide qu'un UPSERT ligne par ligne
            cur.execute("TRUNCATE TABLE tournois_summary")
            psycopg2.extras.execute_values(
                cur,
                """INSERT INTO tournois_summary
                     (id_tournoi, nom, categorie, date_min, date_sort, date_estimee, nb_joueurs, computed_at)
                   VALUES %s""",
                # Les données ont 7 colonnes — computed_at est injecté par NOW() dans le template
                [(t[0], t[1], t[2], t[3], t[4], t[5], t[6]) for t in tuples],
                template="(%s, %s, %s, %s, %s, %s, %s, NOW())",
            )
        conn.commit()
        conn.close()
    else:
        import sqlite3 as _sqlite3
        conn = _sqlite3.connect(os.path.abspath(DB_PATH))
        conn.execute("DELETE FROM tournois_summary")
        conn.executemany(
            """INSERT OR REPLACE INTO tournois_summary
                 (id_tournoi, nom, categorie, date_min, date_sort, date_estimee, nb_joueurs)
               VALUES (?,?,?,?,?,?,?)""",
            [(t[0], t[1], t[2], t[3], t[4], t[5], t[6]) for t in tuples],
        )
        conn.commit()
        conn.close()

    print(f"     → tournois_summary peuplée ({len(tuples)} lignes)", flush=True)


def _call_with_args(fn, **args):
    """Appelle une route Flask avec des query args (via test_request_context)."""
    # On est déjà dans un test_request_context, mais il faut un nouveau avec les args
    # Solution : faire un dict d'env temporaire via werkzeug
    from flask import request
    import api as _api
    qs = "&".join(f"{k}={v}" for k, v in args.items())
    with _api.app.test_request_context(f"/?{qs}"):
        return fn()


if __name__ == "__main__":
    main()
