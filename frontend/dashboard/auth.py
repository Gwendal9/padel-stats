"""
auth.py — Authentification légère pour le dashboard Padel Stats.

Flow magic link :
  1. POST /api/auth/login  → génère un token, envoie email (ou retourne le lien en dev mode)
  2. GET  /api/auth/verify → valide le token, crée une session longue durée
  3. X-Session-Id header   → identifie l'utilisateur sur toutes les routes /api/me/*

Variables d'env pour activer l'envoi email :
  SMTP_HOST, SMTP_PORT (défaut 587), SMTP_USER, SMTP_PASS, SMTP_FROM
  APP_URL (défaut http://localhost:5000) — utilisé pour construire le lien magic

Si SMTP non configuré → le lien est retourné dans la réponse API (mode dev).
"""
import os
import uuid
import datetime
import secrets

from db import get_conn, USE_POSTGRES

# ── Config ────────────────────────────────────────────────────────────────────
TOKEN_TTL_MINUTES = 15
SESSION_TTL_DAYS  = 30

SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
SMTP_FROM = os.environ.get("SMTP_FROM", SMTP_USER)
APP_URL   = os.environ.get("APP_URL", "http://localhost:5000")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.datetime.utcnow().isoformat()

def _token_expires() -> str:
    return (datetime.datetime.utcnow() + datetime.timedelta(minutes=TOKEN_TTL_MINUTES)).isoformat()

def _session_expires() -> str:
    return (datetime.datetime.utcnow() + datetime.timedelta(days=SESSION_TTL_DAYS)).isoformat()

def _expired(ts: str) -> bool:
    try:
        return datetime.datetime.fromisoformat(ts) < datetime.datetime.utcnow()
    except Exception:
        return True

def _exec(conn, sql: str, params: tuple = ()):
    """Execute write SQL, abstracts SQLite vs Postgres placeholder difference."""
    if USE_POSTGRES:
        sql = sql.replace("?", "%s")
        conn.cursor().execute(sql, params)
    else:
        conn.execute(sql, params)

def _query_one(conn, sql: str, params: tuple = ()):
    """Fetch one row as dict, abstracts SQLite vs Postgres."""
    if USE_POSTGRES:
        import psycopg2.extras
        sql = sql.replace("?", "%s")
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row else None
    else:
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None


# ── Magic link ────────────────────────────────────────────────────────────────

def create_magic_link(email: str) -> dict:
    """
    Génère un magic link pour l'email donné.
    Retourne {"link": str, "email_sent": bool, "dev_mode": bool}.
    """
    email = email.strip().lower()
    if not email or "@" not in email:
        raise ValueError("Email invalide")

    token    = secrets.token_urlsafe(32)
    expires  = _token_expires()
    link     = f"{APP_URL}/?token={token}"

    with get_conn(readonly=False) as conn:
        _exec(conn,
            "INSERT OR IGNORE INTO user_tokens(token, email, expires_at, used) VALUES(?,?,?,0)",
            (token, email, expires)
        )
        conn.commit()

    email_sent = _try_send_email(email, link)
    dev_mode   = not bool(SMTP_HOST and SMTP_USER)

    return {
        "link":       link if dev_mode else None,   # On n'expose le lien qu'en dev
        "email_sent": email_sent,
        "dev_mode":   dev_mode,
        "expires_in": TOKEN_TTL_MINUTES,
    }


def _try_send_email(email: str, link: str) -> bool:
    """Envoie le magic link par email. Retourne False si SMTP non configuré."""
    if not SMTP_HOST or not SMTP_USER:
        return False
    try:
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart

        msg = MIMEMultipart("alternative")
        msg["Subject"] = "Ton lien de connexion — Padel Stats"
        msg["From"]    = SMTP_FROM
        msg["To"]      = email

        text = (
            f"Clique sur ce lien pour te connecter (valable {TOKEN_TTL_MINUTES} min) :\n\n{link}\n\n"
            "Si tu n'as pas demandé ce lien, ignore cet email."
        )
        html = f"""
        <html><body style="font-family:system-ui,sans-serif;max-width:480px;margin:40px auto;color:#1e293b">
          <h2 style="color:#6366f1">Padel Stats 🎾</h2>
          <p>Clique sur ce bouton pour te connecter (valable {TOKEN_TTL_MINUTES} minutes) :</p>
          <p style="margin:24px 0">
            <a href="{link}"
               style="background:#6366f1;color:white;padding:12px 28px;border-radius:8px;
                      text-decoration:none;font-weight:600;font-size:15px">
              Se connecter →
            </a>
          </p>
          <p style="color:#94a3b8;font-size:12px">
            Si tu n'as pas demandé ce lien, ignore cet email.
          </p>
        </body></html>
        """
        msg.attach(MIMEText(text, "plain"))
        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as srv:
            srv.ehlo()
            srv.starttls()
            srv.login(SMTP_USER, SMTP_PASS)
            srv.sendmail(SMTP_FROM, email, msg.as_string())
        return True
    except Exception:
        return False


# ── Token verification + session creation ─────────────────────────────────────

def verify_token(token: str) -> dict | None:
    """
    Vérifie un magic link token.
    Si valide : crée/récupère le compte et génère une session.
    Retourne {"session_id", "user_id", "email", "player_fft_id", "is_new"} ou None.
    """
    with get_conn(readonly=False) as conn:
        tok = _query_one(conn,
            "SELECT token, email, expires_at, used FROM user_tokens WHERE token=?",
            (token,)
        )
        if not tok or tok["used"] or _expired(tok["expires_at"]):
            return None

        email = tok["email"]

        # Invalider le token immédiatement (one-time use)
        _exec(conn, "UPDATE user_tokens SET used=1 WHERE token=?", (token,))

        # Créer ou récupérer le compte
        existing = _query_one(conn,
            "SELECT id, email, player_fft_id FROM user_accounts WHERE email=?",
            (email,)
        )
        is_new = False
        if existing:
            user_id        = existing["id"]
            player_fft_id  = existing.get("player_fft_id")
        else:
            user_id       = str(uuid.uuid4())
            player_fft_id = None
            is_new        = True
            _exec(conn,
                "INSERT INTO user_accounts(id, email, created_at) VALUES(?,?,?)",
                (user_id, email, _now())
            )

        # Créer la session
        session_id = secrets.token_urlsafe(32)
        _exec(conn,
            "INSERT INTO user_sessions(session_id, user_id, created_at, last_seen, expires_at)"
            " VALUES(?,?,?,?,?)",
            (session_id, user_id, _now(), _now(), _session_expires())
        )
        conn.commit()

    return {
        "session_id":    session_id,
        "user_id":       user_id,
        "email":         email,
        "player_fft_id": player_fft_id,
        "is_new":        is_new,
    }


# ── Session resolution ─────────────────────────────────────────────────────────

def get_user_from_session(session_id: str) -> dict | None:
    """
    Résout un session_id → infos utilisateur.
    Retourne {"user_id", "email", "player_fft_id", "display_name"} ou None si invalide/expiré.
    """
    if not session_id:
        return None
    with get_conn(readonly=False) as conn:
        row = _query_one(conn, """
            SELECT s.user_id, s.expires_at,
                   u.email, u.player_fft_id, u.display_name
            FROM user_sessions s
            JOIN user_accounts u ON u.id = s.user_id
            WHERE s.session_id = ?
        """, (session_id,))

        if not row or _expired(row["expires_at"]):
            return None

        # Refresh last_seen
        _exec(conn,
            "UPDATE user_sessions SET last_seen=? WHERE session_id=?",
            (_now(), session_id)
        )
        conn.commit()

    return {
        "user_id":       row["user_id"],
        "email":         row["email"],
        "player_fft_id": row.get("player_fft_id"),
        "display_name":  row.get("display_name"),
    }


def invalidate_session(session_id: str):
    """Logout : supprime la session."""
    with get_conn(readonly=False) as conn:
        _exec(conn, "DELETE FROM user_sessions WHERE session_id=?", (session_id,))
        conn.commit()
