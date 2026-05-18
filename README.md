# 🎾 Padel Stats — Dashboard Analytics

Dashboard analytics pour le padel français, construit à partir des données publiques TenUp.
Projet de portfolio Data Analyst.

🚀 **[Accéder au site → padel-stats.onrender.com](https://padel-stats.onrender.com)**

**153 291 joueurs · 1 019 423 participations · 37 124 tournois**

---

## Stack technique

| Couche | Techno |
|--------|--------|
| Scraping | Python (requests + Playwright) |
| Stockage | SQLite (dev) / PostgreSQL (prod) |
| API | Flask + Flask-CORS |
| Frontend | HTML · Tailwind CSS · Chart.js · Leaflet |
| Déploiement | Render (API) + GitHub Pages (front) |

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

## Déploiement sur Render (PostgreSQL)

### 1. Créer la base PostgreSQL sur Neon (gratuit)

1. Créer un compte sur [neon.tech](https://neon.tech)
2. Créer un projet → copier la **Connection string** (format `postgresql://...`)

### 2. Migrer les données SQLite → PostgreSQL

```bash
DATABASE_URL=postgresql://user:pass@host/db python dashboard/migrate_to_postgres.py
```

La migration prend ~5–10 minutes pour 1,2M de lignes.

### 3. Déployer sur Render

1. Fork ce repo sur GitHub
2. Créer un compte [render.com](https://render.com)
3. **New → Web Service** → connecter le repo
4. Render détecte automatiquement `render.yaml`
5. Ajouter `DATABASE_URL` dans les variables d'environnement (valeur Neon)
6. Deploy → l'app est accessible sur `https://padel-stats.onrender.com`

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
