"""
db.py — Connexion DB duale : SQLite (dev local) ou PostgreSQL (production).

En local  : variable DATABASE_URL absente → SQLite via tenup.db
Production: DATABASE_URL=postgresql://... → psycopg2
"""
import os
from contextlib import contextmanager

DATABASE_URL = os.environ.get("DATABASE_URL")
USE_POSTGRES = bool(DATABASE_URL)

if USE_POSTGRES:
    import psycopg2
    import psycopg2.extras
else:
    import sqlite3
    DB_PATH = os.path.join(os.path.dirname(__file__), "..", "tenup.db")


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
