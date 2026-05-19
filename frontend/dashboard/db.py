"""
db.py — Connexion DB duale : SQLite (dev local) ou PostgreSQL (production).

En local  : variable DATABASE_URL absente → SQLite via tenup.db
Production: DATABASE_URL=postgresql://... → psycopg2
"""
import os
from contextlib import contextmanager

DATABASE_URL = os.environ.get("DATABASE_URL")
USE_POSTGRES = bool(DATABASE_URL)

# Timeout SQL par défaut — peut être override par precompute.py via set_statement_timeout()
_PG_STATEMENT_TIMEOUT = "90s"

def set_statement_timeout(value: str):
    """Change le statement_timeout pour les NOUVELLES connexions PG.
    Ex: set_statement_timeout('600s') pour les jobs batch type precompute."""
    global _PG_STATEMENT_TIMEOUT
    _PG_STATEMENT_TIMEOUT = value

# Toujours défini (utilisé par graph_engine et autres modules en mode SQLite)
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "backend", "tenup.db")

if USE_POSTGRES:
    import psycopg2
    import psycopg2.extras
else:
    import sqlite3


def _adapt(query: str, params: tuple) -> tuple[str, tuple]:
    """Adapte la requête et les params pour le backend actif."""
    if USE_POSTGRES:
        # Échapper les % littéraux (ex: LIKE '%foo%') avant de remplacer ? par %s
        # sinon psycopg2 les interprète comme des paramètres → IndexError
        query = query.replace("%", "%%")
        query = query.replace("?", "%s")
        query = query.replace(" LIKE ", " ILIKE ")
    return query, params


@contextmanager
def get_conn(readonly: bool = True):
    if USE_POSTGRES:
        conn = psycopg2.connect(DATABASE_URL)
        # CRITIQUE pour le free tier Render : limite chaque requête à 90s max,
        # sinon une query qui rame bloque le worker indéfiniment et tout l'app gèle.
        # 90s laisse le temps au préchauffage de finir les requêtes lourdes
        # (/api/stats/categories, /api/tournaments) — après c'est cached 10 min.
        try:
            with conn.cursor() as _c:
                _c.execute(f"SET statement_timeout = '{_PG_STATEMENT_TIMEOUT}'")
        except Exception:
            pass
        try:
            yield conn
        finally:
            conn.close()
    else:
        uri = f"file:{os.path.abspath(DB_PATH)}?mode=ro" if readonly else None
        conn = sqlite3.connect(uri if readonly else os.path.abspath(DB_PATH), uri=readonly)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()


def fetchall(query: str, params: tuple = ()) -> list[dict]:
    query, params = _adapt(query, params)
    with get_conn() as conn:
        if USE_POSTGRES:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(query, params)
                return [dict(r) for r in cur.fetchall()]
        else:
            return [dict(r) for r in conn.execute(query, params).fetchall()]


def fetchone(query: str, params: tuple = ()) -> dict | None:
    query, params = _adapt(query, params)
    with get_conn() as conn:
        if USE_POSTGRES:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(query, params)
                row = cur.fetchone()
                return dict(row) if row else None
        else:
            row = conn.execute(query, params).fetchone()
            return dict(row) if row else None


def ensure_indexes():
    """Crée les index manquants et applique les migrations de schéma (idempotent)."""
    # ── PostgreSQL : index essentiels pour le free tier Render ───────────────
    # IF NOT EXISTS rend l'opération idempotente — au pire un no-op de quelques ms.
    # Sans ces index, /api/stats, /api/clubs, /api/leaderboard prennent 30-60s.
    if USE_POSTGRES:
        PG_INDEXES = [
            # Extension trigram pour ILIKE rapide sur nom/prenom (recherche joueur)
            "CREATE EXTENSION IF NOT EXISTS pg_trgm",
            "CREATE INDEX IF NOT EXISTS idx_joueurs_nom_trgm    ON joueurs USING gin(nom gin_trgm_ops)",
            "CREATE INDEX IF NOT EXISTS idx_joueurs_prenom_trgm ON joueurs USING gin(prenom gin_trgm_ops)",
            # Joueurs — filtres fréquents
            "CREATE INDEX IF NOT EXISTS idx_joueurs_classement ON joueurs(classement) WHERE classement IS NOT NULL",
            "CREATE INDEX IF NOT EXISTS idx_joueurs_sexe ON joueurs(sexe)",
            "CREATE INDEX IF NOT EXISTS idx_joueurs_sexe_classement ON joueurs(sexe, classement) WHERE classement IS NOT NULL",
            "CREATE INDEX IF NOT EXISTS idx_joueurs_club ON joueurs(club_nom) WHERE club_nom IS NOT NULL AND club_nom != ''",
            "CREATE INDEX IF NOT EXISTS idx_joueurs_ville ON joueurs(ville) WHERE ville IS NOT NULL AND ville != ''",
            "CREATE INDEX IF NOT EXISTS idx_joueurs_naissance ON joueurs(naissance) WHERE naissance IS NOT NULL",
            # Participations — jointures lourdes
            "CREATE INDEX IF NOT EXISTS idx_parts_joueur ON participations(id_joueur)",
            "CREATE INDEX IF NOT EXISTS idx_parts_tournoi ON participations(id_tournoi)",
            "CREATE INDEX IF NOT EXISTS idx_parts_date ON participations(date_tournoi) WHERE date_tournoi IS NOT NULL",
            # Tournois
            "CREATE INDEX IF NOT EXISTS idx_tournois_nom ON tournois(nom)",
            # Table matérialisée : résumé par tournoi (peuplée par precompute.py)
            # Évite le gros JOIN tournois×participations GROUP BY dans route_tournaments
            # et route_stats_categories (~800k lignes → 3k lignes).
            """CREATE TABLE IF NOT EXISTS tournois_summary (
                id_tournoi  TEXT PRIMARY KEY,
                nom         TEXT,
                categorie   TEXT,
                date_min    TEXT,      -- DD/MM/YYYY (format natif des données)
                date_sort   TEXT,      -- YYYYMMDD   (pour ORDER BY lexicographique)
                nb_joueurs  INTEGER,
                computed_at TIMESTAMPTZ DEFAULT NOW()
            )""",
            "CREATE INDEX IF NOT EXISTS idx_ts_date ON tournois_summary(date_sort)",
            "CREATE INDEX IF NOT EXISTS idx_ts_cat  ON tournois_summary(categorie)",
            # Snapshots mensuels des classements (rempli par le scraper mensuel)
            """CREATE TABLE IF NOT EXISTS classements_historique (
                id          SERIAL PRIMARY KEY,
                id_joueur   TEXT NOT NULL,
                mois        TEXT NOT NULL,
                classement  INTEGER,
                echelon     TEXT,
                variation   INTEGER,
                UNIQUE(id_joueur, mois)
            )""",
            "CREATE INDEX IF NOT EXISTS idx_hist_joueur ON classements_historique(id_joueur)",
            "CREATE INDEX IF NOT EXISTS idx_hist_mois   ON classements_historique(mois)",
            # Table de cache des réponses précalculées (rempli par precompute.py)
            """CREATE TABLE IF NOT EXISTS cache_responses (
                cache_key   TEXT PRIMARY KEY,
                body        TEXT NOT NULL,
                computed_at TIMESTAMP DEFAULT NOW()
            )""",
        ]
        try:
            conn = psycopg2.connect(DATABASE_URL)
            # autocommit pour éviter de bloquer une transaction longue si un index existe déjà
            conn.autocommit = True
            with conn.cursor() as cur:
                for sql in PG_INDEXES:
                    try:
                        cur.execute(sql)
                    except Exception as e:
                        print(f"⚠️  index PG ignoré: {e}")
            conn.close()
            print("✅ Index PG vérifiés/créés")
        except Exception as e:
            print(f"⚠️  ensure_indexes (PG) a échoué (non bloquant): {e}")
        return

    if not USE_POSTGRES:
        try:
            with get_conn(readonly=False) as conn:
                # Index performances
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_joueurs_club ON joueurs(club_nom)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_joueurs_sexe ON joueurs(sexe)"
                )
                # Migration : ajout des colonnes de suivi mensuel
                for migration in [
                    "ALTER TABLE joueurs ADD COLUMN variation_classement INTEGER",
                    "ALTER TABLE joueurs ADD COLUMN classement_date TEXT",
                    # classements_historique : colonne variation ajoutée après création initiale
                    "ALTER TABLE classements_historique ADD COLUMN variation INTEGER",
                ]:
                    try:
                        conn.execute(migration)
                    except Exception:
                        pass  # Colonne déjà présente
                # Table historique mensuel (snapshots)
                # Schéma réel DB : id_joueur (pas id_fft), colonnes echelon + variation
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS classements_historique (
                        id          INTEGER PRIMARY KEY AUTOINCREMENT,
                        id_joueur   TEXT NOT NULL,
                        mois        TEXT NOT NULL,
                        classement  INTEGER,
                        echelon     TEXT,
                        variation   INTEGER,
                        UNIQUE(id_joueur, mois)
                    )
                """)
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_hist_joueur ON classements_historique(id_joueur)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_hist_mois ON classements_historique(mois)"
                )

                # ── Table matérialisée des résumés de tournois ───────────────
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS tournois_summary (
                        id_tournoi  TEXT PRIMARY KEY,
                        nom         TEXT,
                        categorie   TEXT,
                        date_min    TEXT,
                        date_sort   TEXT,
                        nb_joueurs  INTEGER,
                        computed_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_ts_date ON tournois_summary(date_sort)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_ts_cat ON tournois_summary(categorie)"
                )

                # ── Tables utilisateurs / profil / favoris ───────────────────
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS user_accounts (
                        id           TEXT PRIMARY KEY,
                        email        TEXT NOT NULL UNIQUE,
                        player_fft_id TEXT,
                        display_name TEXT,
                        created_at   TEXT NOT NULL
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS user_tokens (
                        token      TEXT PRIMARY KEY,
                        email      TEXT NOT NULL,
                        expires_at TEXT NOT NULL,
                        used       INTEGER NOT NULL DEFAULT 0
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS user_sessions (
                        session_id TEXT PRIMARY KEY,
                        user_id    TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        last_seen  TEXT NOT NULL,
                        expires_at TEXT NOT NULL,
                        FOREIGN KEY(user_id) REFERENCES user_accounts(id)
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS user_favorites (
                        user_id       TEXT NOT NULL,
                        player_fft_id TEXT NOT NULL,
                        added_at      TEXT NOT NULL,
                        PRIMARY KEY (user_id, player_fft_id),
                        FOREIGN KEY(user_id) REFERENCES user_accounts(id)
                    )
                """)
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_sessions_user ON user_sessions(user_id)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_favorites_user ON user_favorites(user_id)"
                )
                conn.commit()
        except Exception:
            pass  # DB en lecture seule ou autre problème non bloquant


# ── Cache des réponses pré-calculées (rempli par precompute.py) ─────────────
def get_cached_body(key: str) -> str | None:
    """Lit le JSON pré-calculé pour ce key. Retourne None si pas en cache."""
    try:
        row = fetchone("SELECT body FROM cache_responses WHERE cache_key = ?", (key,))
        return row["body"] if row else None
    except Exception:
        return None  # table pas encore créée ou autre


def set_cached_body(key: str, body: str) -> None:
    """Stocke (UPSERT) le JSON pour ce key. Utilisé par precompute.py."""
    if USE_POSTGRES:
        sql = ("INSERT INTO cache_responses (cache_key, body, computed_at) "
               "VALUES (%s, %s, NOW()) "
               "ON CONFLICT (cache_key) DO UPDATE SET "
               "body = EXCLUDED.body, computed_at = NOW()")
        conn = psycopg2.connect(DATABASE_URL)
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (key, body))
            conn.commit()
        finally:
            conn.close()
    else:
        with get_conn(readonly=False) as conn:
            conn.execute(
                "INSERT INTO cache_responses (cache_key, body) VALUES (?, ?) "
                "ON CONFLICT(cache_key) DO UPDATE SET body=excluded.body, computed_at=CURRENT_TIMESTAMP",
                (key, body),
            )
            conn.commit()


def fetchval(query: str, params: tuple = ()):
    query, params = _adapt(query, params)
    with get_conn() as conn:
        if USE_POSTGRES:
            with conn.cursor() as cur:
                cur.execute(query, params)
                row = cur.fetchone()
                return row[0] if row else None
        else:
            row = conn.execute(query, params).fetchone()
            return row[0] if row else None
