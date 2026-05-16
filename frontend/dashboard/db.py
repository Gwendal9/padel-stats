"""
db.py — Connexion DB duale : SQLite (dev local) ou PostgreSQL (production).

En local  : variable DATABASE_URL absente → SQLite via tenup.db
Production: DATABASE_URL=postgresql://... → psycopg2
"""
import os
from contextlib import contextmanager

DATABASE_URL = os.environ.get("DATABASE_URL")
USE_POSTGRES = bool(DATABASE_URL)

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
        query = query.replace("?", "%s")
        query = query.replace(" LIKE ", " ILIKE ")
    return query, params


@contextmanager
def get_conn(readonly: bool = True):
    if USE_POSTGRES:
        conn = psycopg2.connect(DATABASE_URL)
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
                ]:
                    try:
                        conn.execute(migration)
                    except Exception:
                        pass  # Colonne déjà présente
                # Table historique mensuel (snapshots)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS classements_historique (
                        id          INTEGER PRIMARY KEY AUTOINCREMENT,
                        id_fft      TEXT NOT NULL,
                        mois        TEXT NOT NULL,
                        classement  INTEGER,
                        variation   INTEGER,
                        meilleur_classement INTEGER,
                        scraped_at  TEXT,
                        UNIQUE(id_fft, mois)
                    )
                """)
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_hist_joueur ON classements_historique(id_fft)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_hist_mois ON classements_historique(mois)"
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
            return row[0] 