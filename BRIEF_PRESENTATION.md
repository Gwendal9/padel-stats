# 🎾 Padel Stats — Brief de présentation pour Claude Design

> Ce document est destiné à Claude Design pour la création d'une présentation visuelle du projet.
> Il contient : le contenu slide par slide, le contexte technique back-end, et les captures à illustrer.

---

## 🎯 CONTEXTE DU PROJET

**Padel Stats** est un projet de portfolio Data Analyst personnel.
L'objectif : construire de A à Z un dashboard analytics sur le padel français,
en scrapant les données publiques de TenUp (plateforme officielle de la FFT — Fédération Française de Tennis).

**L'idée de départ :** Le classement FFT est lisible sur TenUp mais peu exploitable.
Pas de stats, pas de tendances, pas de graphes, pas de comparaison entre joueurs.
→ Padel Stats comble ce vide avec une interface moderne et des fonctionnalités analytiques avancées.

**Audience :** Recruteurs Data / ingénieurs, joueurs de padel amateurs, passionnés.

---

## 📊 CHIFFRES CLÉS (à mettre en valeur visuellement)

| Métrique | Valeur |
|----------|--------|
| Joueurs dans la base | **153 291** |
| Participations à des tournois | **1 019 423** |
| Tournois référencés | **37 124** |
| Nœuds dans le graphe de partenariats | **153k+** |
| Temps de réponse BFS (degrés de séparation) | **< 20ms** |
| Lignes de code Python (back) | ~2 500 |
| Lignes de code HTML/JS (front) | ~4 900 |

---

## 🏗️ ARCHITECTURE TECHNIQUE (pour les slides "Back-end")

### Vue d'ensemble du pipeline

```
[TenUp / FFT]  →  [Scraper Python]  →  [SQLite / PostgreSQL]  →  [API Flask]  →  [Dashboard HTML]
```

### 1. Collecte de données — Le Scraper

**Fichier :** `scraper.py`

**Stratégie :** Cascade BFS depuis un joueur "seed"
- Démarre depuis un joueur connu (ex : id `7633273415`)
- Scrape son profil sur `tenup.fft.fr`
- Découvre ses partenaires via l'API REST FFT (`/back/v1/personnes/joueurs-padel`)
- Ajoute chaque partenaire inconnu à une queue SQLite
- Répète → explore le graphe de proche en proche

**Défis techniques résolus :**
- **Anti-bot / DataDome** : Playwright (navigateur headless Chromium) + cookies Session authentifiés
- **Session Java FFT** : cookies `SHARED_SESSION_JAVA` + `SSESS*` rechargés depuis `cookies.json`
- **Multi-process safe** : queue atomique SQLite avec `worker_id` + mode WAL (Write-Ahead Log)
- **Rate limiting** : délais aléatoires 2.5–5s entre requêtes, blocage des ressources inutiles (images/CSS) pour accélérer
- **Détection de ban** : si 5 profils vides consécutifs → arrêt d'urgence automatique (`STOP` file)

**Technologies :** Python 3.11, Playwright, requests, BeautifulSoup, SQLite WAL

---

### 2. Base de données

**Dev :** SQLite (`tenup.db`) — ~200 MB
**Prod :** PostgreSQL (hébergé sur Neon.tech, gratuit)

**Tables principales :**

| Table | Description | Colonnes clés |
|-------|-------------|---------------|
| `joueurs` | Un joueur = une ligne | `id_fft`, `nom`, `prenom`, `classement`, `meilleur_classement`, `variation_classement`, `club_nom`, `ville`, `sexe`, `naissance` |
| `participations` | Une paire dans un tournoi | `id_joueur`, `partenaire_id`, `id_tournoi`, `position`, `points` |
| `tournois` | Métadonnées tournoi | `id`, `nom`, `categorie`, `date`, `club`, `ville` |
| `classements_historique` | Snapshot mensuel classements | `id_fft`, `mois`, `classement`, `variation` |
| `user_accounts` | Comptes utilisateurs | `email`, `player_fft_id` |
| `user_favorites` | Favoris joueurs | `user_id`, `player_fft_id` |

**Migration SQLite → PostgreSQL :** `migrate_to_postgres.py` (~5–10 min pour 1,2M de lignes)

---

### 3. API Flask

**Fichier :** `dashboard/api.py`

**Endpoints :**

| Route | Fonction |
|-------|----------|
| `GET /api/search?q=ROLLAND` | Recherche joueurs (nom/prénom, LIKE insensible casse) |
| `GET /api/player/<id>` | Profil complet joueur (stats, trophées, partenaires, historique) |
| `GET /api/suggest/<id>` | Suggestions partenaires (score multi-critères) |
| `GET /api/path/<src>/<tgt>` | Degrés de séparation BFS entre deux joueurs |
| `GET /api/ego/<id>?depth=2` | Graphe ego D3.js (nœuds + liens jusqu'à N degrés) |
| `GET /api/leaderboard` | Classement paginé avec filtres (région, club, âge, sexe) |
| `GET /api/movers?sexe=H` | Top progressions / régressions de classement |
| `GET /api/stats` | Stats globales pour le dashboard |
| `GET /api/clubs?top=100` | Classement des clubs |

**Optimisation au démarrage :**
- Graphe de partenariats chargé en mémoire dans un thread daemon (~3s)
- Index SQL créés automatiquement (idempotent)
- Support dual SQLite/PostgreSQL via `db.py` (une seule fonction `fetchall()`)

---

### 4. Moteur de graphe — `graph_engine.py`

Le cœur algorithmique du projet.

**Structure :** `dict[str, dict[str, int]]` — adjacence pondérée en mémoire
- Clé = `id_fft` joueur
- Valeur = dict `{id_voisin: nb_tournois_en_commun}`

**Fonctions :**
- `load()` : charge 153k joueurs + tous les liens de partenariat depuis la DB
- `shortest_path(src, tgt)` : BFS classique → retourne le chemin + liste d'étapes
- `ego_graph(id, depth)` : exploration BFS jusqu'à N degrés → retourne `{nodes, links}` pour D3.js

**Perf :** BFS sur 153k nœuds < 20ms grâce à la structure en mémoire.

---

### 5. Suggesteur de partenaires — `suggester.py`

Système de recommandation basé sur un score composite (0–100) :

| Critère | Points |
|---------|--------|
| Même ville | +40 |
| Même département | +25 |
| Même région | +10 |
| Niveau proche (±20% classement) | +30 → 0 décroissant |
| Ami d'ami dans le graphe | +15 |
| Âge proche (±5 ans) | +5 |
| **Requis :** Jamais joué ensemble | exclusion |
| **Requis :** Joueur actif (≥ 3 tournois) | exclusion |

---

### 6. Authentification utilisateur — `auth.py` + `user_data.py`

Système magic link (sans mot de passe) :
- Envoi d'un lien par email → token JWT à usage unique
- Session persistante (~30 jours)
- Chaque utilisateur peut lier son compte à son `id_fft` FFT
- Favoris : suivi de joueurs

---

### 7. Déploiement

| Composant | Service |
|-----------|---------|
| API Flask | Render.com (Web Service) |
| Base de données prod | Neon.tech (PostgreSQL serverless, gratuit) |
| Frontend | GitHub Pages (HTML statique) |
| Config déploiement | `render.yaml` + `Procfile` |

---

## 🖥️ FONCTIONNALITÉS FRONT-END (captures à illustrer)

Le frontend est une **Single Page Application** HTML/Tailwind/Chart.js dans un seul fichier (`dashboard_mockup.html`, ~4900 lignes).
Navigation par sections (sidebar gauche).

### Vue 1 — Tableau de bord (page d'accueil)
**Ce qu'on voit :**
- KPI cards : nb joueurs, nb tournois, nb participations
- Mini-classement H + F (top 5 avec variation de classement)
- Graphique "Top progressions du mois"
- Bouton CTA vers le profil joueur

**Captures à montrer :** Cards de stats, mini-leaderboard avec badges ▲▼

---

### Vue 2 — Classement national
**Ce qu'on voit :**
- Tabs H / F (Hommes / Femmes — strictement séparés)
- Filtres : région, département, club, tranche d'âge
- Tableau paginé avec infinite scroll
- Colonne variation classement avec indicateurs ▲▼
- Badge "Record perso" si le joueur est à son meilleur classement
- Mode "Par clubs" : classement des clubs avec tri multi-critères

**Captures à montrer :** Tableau de classement avec filtres actifs, badge record

---

### Vue 3 — Profil joueur
**Ce qu'on voit :**
- Avatar initial + infos (nom, ville, club, âge)
- Classement actuel + meilleur classement + variation mensuelle
- Percentile dans la population (ex : "Top 8%")
- KPIs : nb tournois joués, points cumulés, position moyenne
- **Trophy shelf** : top 5 résultats par catégorie de tournoi (P25, P50, P100…)
- **Graphique chronologique** : évolution du classement sur 12 mois
- **Top partenaires** : les joueurs avec qui il a le plus joué
- **Distribution des positions** : camembert victoires/finales/demies/quarts…
- **Championnats** : meilleurs résultats en épreuves par équipes

**Captures à montrer :** Profil complet avec trophy shelf, graphique d'évolution

---

### Vue 4 — Suggesteur de partenaires
**Ce qu'on voit :**
- Recherche d'un joueur
- Breadcrumb de navigation
- Cartes de suggestions avec score (0–100) et explication ("Même ville · Niveau proche · Ami de X")
- Avatar coloré, club, classement, nb tournois communs potentiels

**Captures à montrer :** Grille de suggestions avec scores et explication

---

### Vue 5 — Degrés de séparation
**Ce qu'on voit :**
- Deux champs de recherche (joueur A → joueur B)
- Résultat : chemin visuel A → intermédiaire 1 → intermédiaire 2 → B
- Chaque étape = avatar + nom + lien cliquable vers le profil
- Temps de calcul affiché (< 20ms)

**Captures à montrer :** Chemin de séparation visuel entre deux joueurs

---

### Vue 6 — Graphe ego (D3.js)
**Ce qu'on voit :**
- Visualisation force-directed D3.js
- Nœud central = joueur sélectionné
- 1er degré = partenaires directs (avec nb tournois en commun)
- 2ème degré = partenaires des partenaires
- Zoom / drag / hover avec tooltip (nom + classement)
- Sélecteur de profondeur (1, 2, 3 degrés)

**Captures à montrer :** Graphe force-directed avec le joueur central

---

### Vue 7 — Carte de France (Leaflet.js)
**Ce qu'on voit :**
- Carte interactive avec marqueurs par club
- Taille des marqueurs proportionnelle au nb de joueurs
- Popup au clic : nom du club, nb joueurs, meilleur classement

**Captures à montrer :** Carte avec clusters de marqueurs

---

### Vue 8 — Top progressions
**Ce qu'on voit :**
- Période : 30j / 3 mois / 12 mois
- Tabs H / F
- Podium visuel top 3 (avec photos de profil générées)
- Liste des 8 plus grandes progressions et 8 plus grandes régressions

**Captures à montrer :** Podium des progressions avec cartes stylisées

---

## 🎨 DIRECTION ARTISTIQUE SUGGÉRÉE

**Couleurs du projet :**
- Primary : `#0d9488` (teal-600) — couleur principale
- Accent : `#f59e0b` (amber-500) — trophées, records
- Background : `#f8fafc` — gris très clair
- Surface : `#ffffff`
- Text : `#0f172a` — quasi-noir

**Typographie :** Inter (Google Fonts) — 400/500/600/700/800

**Style UI :** Clean / moderne / data-driven. Cards avec légère ombre, bordures `#e2e8f0`, border-radius 12px. Pas de gradients agressifs. Tons teal + blanc + ardoise.

**Mood à viser pour la présentation :** Portfolio sérieux d'un Data Analyst, impression de maîtrise technique + sens du produit. Sobre mais impressionnant par la profondeur fonctionnelle.

---

## 📐 STRUCTURE SUGGÉRÉE POUR LA PRÉSENTATION (10–12 slides)

| # | Titre slide | Contenu |
|---|-------------|---------|
| 1 | **Padel Stats** | Titre + tagline + chiffres clés (153k joueurs, 1M+ participations) |
| 2 | **Le problème** | TenUp = données riches mais peu exploitables. Pas de stats, pas de tendances. |
| 3 | **La solution** | Dashboard analytics complet : classement, profils, graphes, IA de suggestion |
| 4 | **Architecture** | Schéma pipeline : Scraper → DB → Flask API → Frontend |
| 5 | **Le Scraper** | Cascade BFS, Playwright, anti-détection, multi-process |
| 6 | **La base de données** | Schéma tables + chiffres (1,2M lignes) + migration SQLite/PostgreSQL |
| 7 | **L'API Flask** | Liste endpoints + optimisations (graphe en mémoire, BFS < 20ms) |
| 8 | **Dashboard — Classement** | Capture + description filtres, séparation H/F, infinite scroll |
| 9 | **Dashboard — Profil joueur** | Capture + trophy shelf, percentile, évolution chronologique |
| 10 | **Dashboard — Graphe & Suggesteur** | Capture D3.js + algo de scoring partenaires |
| 11 | **Déploiement** | Render + Neon PostgreSQL + GitHub Pages. Stack 100% gratuite. |
| 12 | **Ce que ça démontre** | Compétences : scraping, SQL, API, algo graphe, product thinking, déploiement |

---

## 💡 NOTES POUR CLAUDE DESIGN

- Les captures d'écran n'existent pas encore — tu peux créer des **mockups visuels** fidèles au design décrit (sidebar teal foncé, contenu blanc, cartes grises)
- Les couleurs sont `#0d9488` (teal), `#f59e0b` (amber), `#0f172a` (texte), `#f8fafc` (fond)
- Le style est "data dashboard moderne" — pense Vercel, Linear, PlanetScale dans leur style de présentation
- Insister sur la **cohérence bout en bout** : du scraping à l'UX, tout a été fait par une seule personne
- La feature la plus impressionnante techniquement = **graphe en mémoire 153k nœuds + BFS < 20ms**
- La feature la plus "product" = **suggesteur de partenaires** avec scoring multi-critères expliqué
