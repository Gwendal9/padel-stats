# 🎾 Padel Stats — Dashboard Analytics

Dashboard analytics pour le padel français, construit à partir des données publiques TenUp.
Projet de portfolio Data Analyst.

<<<<<<< Updated upstream
🚀 **[Accéder au site → padel-stats.onrender.com](https://padel-stats-oava.onrender.com/)**
=======
🚀 **[Accéder au site → padel.gwendev.eu](https://padel.gwendev.eu)**
>>>>>>> Stashed changes

**153 291 joueurs · 1 019 423 participations · 37 124 tournois**

---

## Stack technique

| Couche | Techno |
|--------|--------|
| Scraping | Python (requests + Playwright) |
| Stockage | SQLite |
| API | Flask + Flask-CORS |
| Frontend | HTML · Tailwind CSS · Chart.js · Leaflet |
| Déploiement | VPS Hetzner (Docker) + Cloudflare Tunnel |

---

## Fonctionnalités

- **Classement national** avec filtres région / club / âge / sexe
- **Profil joueur** : stats, trophy shelf, parcours chronologique, top % percentile
- **Suggesteur de partenaires** : recommandation basée sur le graphe de co-participations
- **Degrés de séparation** : BFS sur le graphe de 153k joueurs (< 20ms)
- **Graphe ego** : visualisation D3.js des 2 degrés de connexion d'un joueur
- **Onglet tournoi** : podium top 5, distribution des paires, bracket
- **Top progressions** : les plus grosses montées de classement (30j / 3 mois / 12 mois)

---

## Lancer en local

### Prérequis
```bash
python 3.11+
pip install -r dashboard/requirements.txt
```

### Démarrage
```bash
# Lance le serveur Flask (port 5000)
cd dashboard && python api.py

# Ouvre le dashboard
open http://localhost:5000
```

La base SQLite (`tenup.db`) est chargée automatiquement. Le graphe en mémoire (~3s au démarrage) permet des requêtes BFS < 20ms.

---

## Déploiement sur VPS (Docker + Cloudflare Tunnel)

### Prérequis
- VPS avec Docker installé
- Cloudflare Tunnel configuré (`cloudflared`)
- DB SQLite disponible sur le VPS

### 1. Copier la DB sur le VPS

```bash
scp backend/tenup.db root@<vps-ip>:/opt/padel-data/tenup.db
```

### 2. Déployer

```bash
ssh root@<vps-ip>
git clone https://github.com/Gwendal9/padel-stats.git /opt/padel
cd /opt/padel && git checkout dev
docker compose up -d --build
```

### 3. Cloudflare Tunnel (`/etc/cloudflared/config.yml`)

```yaml
ingress:
  - hostname: padel.gwendev.eu
    service: http://localhost:5000
  - service: http_status:404
```

```bash
systemctl restart cloudflared
```

### Mise à jour de la DB

```bash
scp backend/tenup.db root@<vps-ip>:/opt/padel-data/tenup.db
# Pas besoin de redémarrer le container
```

---

## Structure du projet

```
├── dashboard_mockup.html      ← Frontend (single-file)
├── dashboard/
│   ├── api.py                 ← Flask API (routes + serve HTML)
│   ├── db.py                  ← Connexion DB duale SQLite/PostgreSQL
│   ├── player_profile.py      ← Profil joueur & recherche
│   ├── graph_engine.py        ← BFS + graphe ego (in-memory)
│   ├── suggester.py           ← Suggesteur de partenaires
│   ├── data_builder.py        ← Export JSON statiques (stats globales)
│   ├── migrate_to_postgres.py ← Migration SQLite → PostgreSQL
│   └── requirements.txt
├── render.yaml                ← Config déploiement Render
├── Procfile                   ← Commande de démarrage
└── .env.example               ← Template variables d'environnement
```

---

## Variables d'environnement

| Variable | Description | Défaut |
|----------|-------------|--------|
| `DATABASE_URL` | URL PostgreSQL. Si vide → SQLite local | *(vide = SQLite)* |
| `FLASK_ENV` | `development` ou `production` | `development` |
| `PORT` | Port d'écoute du serveur | `5000` |

---

## API endpoints

| Route | Description |
|-------|-------------|
| `GET /` | Dashboard HTML |
| `GET /api/search?q=ROLLAND` | Recherche joueurs |
| `GET /api/player/<id>` | Profil complet joueur |
| `GET /api/suggest/<id>` | Suggestions partenaires |
| `GET /api/path/<src>/<tgt>` | Degrés de séparation (BFS) |
| `GET /api/ego/<id>?depth=2` | Graphe ego (nœuds + liens) |
| `GET /api/health` | Statut de l'API |

---

*Données issues de TenUp (fédération française de padel). Usage personnel / portfolio.*
