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
    from db import set_cached_body, USE_POSTGRES, ensure_indexes, set_statement_timeout

    # Job batch : pas de timeout SQL (les requêtes peuvent prendre plusieurs minutes)
    set_statement_timeout("0")  # 0 = illimité en PostgreSQL

    # S'assurer que la table cache_responses existe
    ensure_indexes()

    # Liste des (cache_key, fonction_route) à pré-calculer
    JOBS = [
        ("stats",            api.route_stats),
        ("stats_categories", api.route_stats_categories),
        ("tournaments_20",   lambda: _call_with_args(api.route_tournaments, limit="20")),
        ("clubs_100",        lambda: _call_with_args(api.route_clubs,       top="100")),
        ("clubs_1000",       lambda: _call_with_args(api.route_clubs,       top="1000")),
    ]

    print(f"\n🚀 Pré-calcul démarré ({len(JOBS)} jobs) — DATABASE = "
          f"{'PostgreSQL' if USE_POSTGRES else 'SQLite local'}\n", flush=True)
    t_total = time.time()

    for key, fn in JOBS:
        t0 = time.time()
        print(f"⏳ [{key}] calcul en cours…", flush=True)
        try:
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
