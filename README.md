# 🧭 Padel Repère — Dashboard Analytics

Dashboard analytics pour le padel français, construit à partir des classements officiels publics de la FFT.
Projet personnel, sans lien officiel avec la FFT.

🚀 **[Accéder au site → padel.gwendev.eu](https://padel.gwendev.eu)**

**185 000+ joueurs classés · 5 400+ clubs · 56 000+ tournois**

---

## Stack technique

| Couche | Techno |
|--------|--------|
| Scraping | Python (requests, cookies FFT) |
| Stockage | SQLite |
| API | Flask |
| Frontend | HTML · Tailwind CSS · Chart.js · Leaflet · D3.js |
| Déploiement | VPS Hetzner (Docker + nginx) + Cloudflare Tunnel |

---

## Fonctionnalités

- **Classements filtrables** — joueurs, clubs, tournois (région / département / club / sexe), avec évolution mensuelle
- **Fiche joueur** — classement, forme du moment, points à défendre, parcours pondéré par la difficulté des tournois, partenaires, trajectoire mois par mois
- **Historique « hors bilan »** — les performances de plus de 12 mois disparaissent du bilan FFT, mais restent visibles ici (données que la FFT elle-même ne montre plus)
- **Fiche tournoi** — podium, difficulté réelle du plateau, reconstitution robuste des paires
- **Fiche club** — meilleurs joueurs, niveau moyen, tournois organisés
- **Carte de France** — choroplèthe par département (joueurs / clubs / classement moyen), zoom par commune
- **Graphe de jeu** — degrés de séparation entre joueurs, suggesteur de partenaires (BFS sur le graphe de co-participations)
- **Favoris** — stockés localement dans le navigateur, sans compte à créer
- **Fiches au hasard** — pour explorer le site sans chercher un nom précis

---



## Structure du projet

```
├── backend/
│   ├── scraper_json.py        ← Scraper FFT (privé, non versionné)
│   ├── cleanup_db.py          ← Dédoublonnage des participations
│   ├── build_timeline.py      ← Séries temporelles (courbe d'évolution, nouveaux classés)
│   ├── build_geo.py / geocode_villes.py  ← Couche géographique (carte)
│   ├── tournois_rating.py     ← Difficulté réelle des tournois
│   └── match_partenaires.py   ← Détection des binômes
├── frontend/dashboard/
│   ├── api.py                 ← API Flask (routes + pages)
│   ├── player_profile.py      ← Profil joueur (forme, parcours, hors bilan…)
│   ├── db.py                  ← Connexion DB (SQLite, lecture seule)
│   ├── home.html, fiche.html, carte.html, classement.html,
│   │   club.html, tournoi.html, clubs.html, tournois.html, graphe.html
│   └── static/                ← CSS/JS partagés
├── docker-compose.yml
├── Dockerfile
└── nginx.conf
```

---

## Variables d'environnement

| Variable | Description |
|----------|-------------|
| `ADMIN_KEY` | Clé pour les routes d'administration (`/api/admin/*`) |
| `DISCORD_WEBHOOK_URL` | Notification Discord pour les suggestions utilisateurs |

Chargées depuis un fichier `.env` sur le VPS (voir `.env.example`), jamais versionnées.

---

## Principaux endpoints API

| Route | Description |
|-------|-------------|
| `GET /api/search?q=...` | Recherche joueurs |
| `GET /api/player/<id>` | Profil complet joueur |
| `GET /api/tournoi/<id>` | Détail d'un tournoi (paires, podium, difficulté) |
| `GET /api/club?nom=...` | Détail d'un club |
| `GET /api/leaderboard` | Classement filtrable |
| `GET /api/geo/departements` | Données pour la carte de France |
| `GET /api/suggest/<id>` | Suggestions de partenaires |
| `GET /api/path/<src>/<tgt>` | Degrés de séparation (BFS) |
| `GET /api/health` | Statut de l'API |

---

*Données issues des classements publics de la Fédération Française de Tennis (padel). Usage personnel, non commercial — voir les [mentions légales](https://padel.gwendev.eu/mentions).*
