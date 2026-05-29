# ── Padel Stats — Dockerfile ──────────────────────────────────────────────────
# App : frontend/dashboard/api.py (Flask + Gunicorn, SQLite)
# DB  : montée en volume externe → /app/backend/tenup.db

FROM python:3.11-slim

# Dépendances système minimales
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Layer de dépendances Python (mis en cache tant que requirements.txt ne change pas)
COPY frontend/dashboard/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Code applicatif
COPY frontend/ frontend/

# Dossier backend créé (la DB sera montée en volume, pas copiée dans l'image)
RUN mkdir -p backend

WORKDIR /app/frontend/dashboard

EXPOSE 5000

CMD ["gunicorn", "api:app", \
     "--workers", "1", \
     "--timeout", "300", \
     "--bind", "0.0.0.0:5000", \
     "--access-logfile", "-"]
