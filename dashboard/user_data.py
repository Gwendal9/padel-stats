"""
user_data.py — Gestion des données utilisateur : favoris et lien profil FFT.
"""
import datetime

from db import get_conn, fetchall, fetchone, USE_POSTGRES


def _now() -> str:
    return datetime.datetime.utcnow().isoformat()


def _exec(conn, sql: str, params: tuple = ()):
    if USE_POSTGRES:
        sql = sql.replace("?", "%s")
        conn.cursor().execute(sql, params)
    else:
        conn.execute(sql, params)


# ── Lien joueur FFT ───────────────────────────────────────────────────────────

def link_player(user_id: str, player_fft_id: str, display_name: str = None) -> bool:
    """Lie un joueur FFT au compte utilisateur."""
    with get_conn(readonly=False) as conn:
        _exec(conn,
            "UPDATE user_accounts SET player_fft_id=?, display_name=? WHERE id=?",
            (player_fft_id, display_name, user_id)
        )
        conn.commit()
    return True


def unlink_player(user_id: str) -> bool:
    """Délie le profil FFT du compte utilisateur."""
    with get_conn(readonly=False) as conn:
        _exec(conn,
            "UPDATE user_accounts SET player_fft_id=NULL, display_name=NULL WHERE id=?",
            (user_id,)
        )
        conn.commit()
    return True


def update_display_name(user_id: str, display_name: str) -> bool:
    with get_conn(readonly=False) as conn:
        _exec(conn,
            "UPDATE user_accounts SET display_name=? WHERE id=?",
            (display_name.strip()[:80], user_id)
        )
        conn.commit()
    return True


# ── Favoris ───────────────────────────────────────────────────────────────────

def get_favorites(user_id: str) -> list[dict]:
    """Retourne les joueurs favoris avec leurs infos de base."""
    rows = fetchall("""
        SELECT f.player_fft_id, f.added_at,
               j.nom, j.prenom, j.classement, j.club_nom, j.ville, j.sexe,
               j.meilleur_classement, j.variation_classement
        FROM user_favorites f
        LEFT JOIN joueurs j ON j.id_fft = f.player_fft_id
        WHERE f.user_id = ?
        ORDER BY f.added_at DESC
    """, (user_id,))
    return [_fmt_favorite(r) for r in rows]


def _fmt_favorite(r: dict) -> dict:
    prenom = (r.get("prenom") or "").strip()
    nom    = r.get("nom") or ""
    return {
        "id":                   r["player_fft_id"],
        "nom_complet":          f"{prenom} {nom}".strip(),
        "classement":           r.get("classement"),
        "meilleur_classement":  r.get("meilleur_classement"),
        "variation_classement": r.get("variation_classement"),
        "club":                 r.get("club_nom") or "",
        "ville":                r.get("ville") or "",
        "sexe":                 r.get("sexe") or "",
        "added_at":             r.get("added_at"),
    }


def add_favorite(user_id: str, player_fft_id: str) -> bool:
    """Ajoute aux favoris. Retourne True (idempotent)."""
    with get_conn(readonly=False) as conn:
        if USE_POSTGRES:
            sql = "INSERT INTO user_favorites(user_id, player_fft_id, added_at) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING"
            conn.cursor().execute(sql, (user_id, player_fft_id, _now()))
        else:
            conn.execute(
                "INSERT OR IGNORE INTO user_favorites(user_id, player_fft_id, added_at) VALUES(?,?,?)",
                (user_id, player_fft_id, _now())
            )
        conn.commit()
    return True


def remove_favorite(user_id: str, player_fft_id: str) -> bool:
    """Retire un joueur des favoris."""
    with get_conn(readonly=False) as conn:
        _exec(conn,
            "DELETE FROM user_favorites WHERE user_id=? AND player_fft_id=?",
            (user_id, player_fft_id)
        )
        conn.commit()
    return True


def is_favorite(user_id: str, player_fft_id: str) -> bool:
    row = fetchone(
        "SELECT 1 FROM user_favorites WHERE user_id=? AND player_fft_id=?",
        (user_id, player_fft_id)
    )
    return row is not None


def get_favorites_ids(user_id: str) -> set[str]:
    """Retourne juste les IDs FFT favoris (pour affichage bulk)."""
    rows = fetchall(
        "SELECT player_fft_id FROM user_favorites WHERE user_id=?",
        (user_id,)
    )
    return {r["player_fft_id"] for r in rows}
