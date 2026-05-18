"""
migrate_to_postgres.py — Migration SQLite → PostgreSQL.

Usage :
  DATABASE_URL=postgresql://user:pass@host/db python migrate_to_postgres.py

Migre les tables : joueurs, participations, tournois
(scrape_queue et autres tables internes sont ignorées)
"""
import os
import sqlite3
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras

DB_PATH = Path(__file__).parent.parent.parent / "backend" / "tenup.db"
DATABASE_URL = os.environ.get("DATABASE_URL")
TABLES = ["tournois", "joueurs", "participations"]
BATCH = 2000  # lignes par INSERT

# ── Types SQLite → PostgreSQL ─────────────────────────────────────────────────
TYPE_MAP = {
    "TEXT": "TEXT",
    "INTEGER": "INTEGER",
    "REAL": "DOUBLE PRECISION",
    "NUMERIC": "NUMERIC",
    "BLOB": "BYTEA",
    "": "TEXT",
}


def sqlite_type_to_pg(t: str) -> str:
    return TYPE_MAP.get(t.upper().split("(")[0].strip(), "TEXT")


def get_schema(sq_conn, table: str) -> list[tuple]:
    """Retourne [(col_name, pg_type, is_pk), ...]"""
    cols = sq_conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [(c[1], sqlite_type_to_pg(c[2]), bool(c[5])) for c in cols]


def create_table(pg_conn, table: str, schema: list[tuple]):
    pk_cols = [c[0] for c in schema if c[2]]
    col_defs = ", ".join(f'"{c[0]}" {c[1]}' for c in schema)
    pk_quoted = ", ".join('"' + c + '"' for c in pk_cols)
    pk_clause = f", PRIMARY KEY ({pk_quoted})" if pk_cols else ""
    ddl = f'CREATE TABLE IF NOT EXISTS "{table}" ({col_defs}{pk_clause});'
    with pg_conn.cursor() as cur:
        cur.execute(f'DROP TABLE IF EXISTS "{table}" CASCADE;')
        cur.execute(ddl)
    pg_conn.commit()


def migrate_table(sq_conn, pg_conn, table: str):
    schema = get_schema(sq_conn, table)
    col_names = [c[0] for c in schema]

    print(f"  → {table} : création de la table PG...")
    create_table(pg_conn, table, schema)

    total = sq_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    print(f"  → {table} : {total:,} lignes à migrer...")

    cols_sql = ", ".join(f'"{c}"' for c in col_names)
    placeholders = ", ".join(["%s"] * len(col_names))
    insert_sql = f'INSERT INTO "{table}" ({cols_sql}) VALUES ({placeholders}) ON CONFLICT DO NOTHING'

    rows = sq_conn.execute(f"SELECT {cols_sql} FROM {table}").fetchmany
    offset = 0
    migrated = 0

    while True:
        batch = sq_conn.execute(
            f"SELECT {cols_sql} FROM {table} LIMIT {BATCH} OFFSET {offset}"
        ).fetchall()
        if not batch:
            break
        with pg_conn.cursor() as cur:
            psycopg2.extras.execute_batch(cur, insert_sql, [tuple(r) for r in batch])
        pg_conn.commit()
        migrated += len(batch)
        offset += BATCH
        pct = migrated / total * 100
        print(f"    {migrated:,}/{total:,} ({pct:.1f}%)", end="\r")

    print(f"    {migrated:,}/{total:,} ✅                    ")


def main():
    if not DATABASE_URL:
        print("❌ Variable DATABASE_URL manquante.")
        print("   Usage : DATABASE_URL=postgresql://... python migrate_to_postgres.py")
        sys.exit(1)

    if not DB_PATH.exists():
        print(f"❌ Base SQLite introuvable : {DB_PATH}")
        sys.exit(1)

    print(f"Source  : {DB_PATH}")
    print(f"Cible   : {DATABASE_URL[:40]}...")
    print()

    sq_conn = sqlite3.connect(str(DB_PATH))
    pg_conn = psycopg2.connect(DATABASE_URL)

    for table in TABLES:
        migrate_table(sq_conn, pg_conn, table)

    sq_conn.close()
    pg_conn.close()

    print("\n✅ Migration terminée.")
    print("   Lance ensuite : DATABASE_URL=... python api.py")


if __name__ == "__main__":
    main()
