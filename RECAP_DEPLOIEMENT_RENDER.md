# Récap — Problème de chargement Padel Stats sur Render

> Document à partager au début d'un nouveau chat pour repartir efficacement.

---

## 1. Contexte projet

**App** : `padel-stats` — dashboard Flask + HTML (Tailwind, Chart.js) qui affiche les statistiques du padel français (issu d'un scrape FFT).

**Stack** :
- Backend : Flask + gunicorn (`gthread`, 1 worker, 4 threads, timeout 120s)
- DB : PostgreSQL Render free tier OU SQLite local (selon `DATABASE_URL`)
- Frontend : `dashboard_mockup.html` (HTML monolithique avec JS inline)
- Hébergement : Render free tier (0.1 CPU, 512MB RAM, IO partagé)
- URL : https://padel-stats-oava.onrender.com

**Volume de données** :
- ~149 000 joueurs (table `joueurs`)
- ~800 000 participations (table `participations`)
- ~3 000 tournois (table `tournois`)

**Structure repo** :
```
G:\tenup_scraper\tenup_scraper_v2\
├── frontend/
│   ├── dashboard/
│   │   ├── api.py              # routes Flask
│   │   ├── db.py               # abstraction SQLite/Postgres
│   │   ├── graph_engine.py     # moteur de graphe partenaires
│   │   ├── player_profile.py   # recherche & profil joueur
│   │   ├── precompute.py       # NOUVEAU : pré-calcul des stats
│   │   └── requirements.txt
│   └── dashboard_mockup.html   # UI complète (1 fichier)
├── backend/                     # scrapers (Python local)
├── render.yaml                  # config déploiement
└── .git/
```

---

## 2. Problème principal

**Symptôme** : les KPI et tableaux du dashboard ne s'affichent jamais (chargements infinis, erreurs console). Endpoints lourds (`/api/stats`, `/api/stats/categories`, `/api/tournaments`, `/api/clubs?top=1000`) timeout systématiquement.

**Cause racine identifiée** : le free tier PostgreSQL Render est structurellement trop lent pour les requêtes du projet :
- `SELECT COUNT(DISTINCT id_tournoi) FROM participations` → 20-30s
- `JOIN tournois × participations GROUP BY` → > 90s (kill par timeout)
- Même les requêtes "simples" type counters prennent 10-20s

---

## 3. Architecture cible mise en place

**Approche choisie** : pré-calcul des réponses lourdes dans une table Postgres.

1. **Table `cache_responses(cache_key TEXT PRIMARY KEY, body TEXT, computed_at TIMESTAMP)`** — créée auto par `ensure_indexes()` au boot.

2. **Script `frontend/dashboard/precompute.py`** — calcule les 5 réponses lourdes UNE FOIS et les stocke dans `cache_responses`. Sans timeout SQL pour ce job batch.

3. **Endpoints Flask modifiés** — lisent d'abord `cache_responses` via `_try_precomputed(key)` (instantané ~5ms). Fallback : calcul live + cache mémoire 10 min.

4. **Endpoint admin** : `GET /api/admin/precompute?key=$ADMIN_KEY` lance le script en thread daemon (réponse "started" immédiate, job tourne 5-10 min en background).

5. **Variable d'env requise** : `ADMIN_KEY` (à créer dans Render Environment, valeur libre).

**Lancement après chaque scrape mensuel** :
```
https://padel-stats-oava.onrender.com/api/admin/precompute?key=XXX
```

---

## 4. Tout ce qu'on a testé chronologiquement

### Étape 1 — Index Postgres automatiques (db.py)
Avant : `ensure_indexes()` retournait early pour Postgres → aucun index → seq scans partout.
Maintenant : crée 10 index essentiels (`joueurs.classement`, `joueurs.sexe_classement`, `joueurs.club_nom`, `participations.id_joueur`, `participations.id_tournoi`, etc.) avec `IF NOT EXISTS`.
**Résultat** : amélioration mais insuffisant.

### Étape 2 — Robustifier apiFetch côté front (dashboard_mockup.html, ligne ~3030)
- Remplacé `AbortSignal.timeout()` par `AbortController + setTimeout` (compat browser)
- Timeout par défaut : 10s → 30s
- Retry automatique 1× sur `TimeoutError`/`AbortError`/`TypeError`/`503`
- Parsing JSON safe (gère HTML d'erreur)
**Résultat** : moins de "ReferenceError" mystérieux mais timeouts persistent.

### Étape 3 — Cache mémoire 10 min sur endpoints lourds
Ajout d'un cache `_MEM_CACHE` dans api.py. Évite recalculs successifs.
**Résultat** : utile mais ne résout pas le 1er hit.

### Étape 4 — Préchauffage in-memory au boot (`_preheat_caches` thread)
Thread daemon qui appelle les routes lourdes via `test_request_context` au démarrage.
**Résultat** : ❌ saturait le worker, OOM possible, master kill par Render. → **Supprimé.**

### Étape 5 — `statement_timeout` Postgres (45s puis 90s)
Toute requête > 90s est killée → l'app ne gèle plus indéfiniment.
**Résultat** : les requêtes lourdes (categories, tournaments) timeoutent quand même.

### Étape 6 — Architecture pré-calcul (état actuel)
Table `cache_responses` + script `precompute.py` + endpoint `/api/admin/precompute`.
**Avantage** : transforme requêtes 60-180s en lectures 5ms.
**Inconvénient** : nécessite un job manuel après chaque scrape (acceptable car snapshot mensuel).

---

## 5. État actuel (au moment de cette synthèse)

✅ Code déployé sur Render
✅ Variable `ADMIN_KEY` configurée
✅ `/api/admin/precompute?key=XXX` répond `{"status":"started"}`
⏳ Job en cours (logs Render montrent `[stats] counts: 25.1s`, `[stats] ranking: 3.6s`, `[stats] pyramide: 9.7s`...)
❌ Dashboard pas encore réactif (le precompute n'a pas encore fini, OU les `/api/tournaments` et `/api/stats/categories` continuent de timeout dans le precompute aussi).

**Logs problématiques persistants** :
```
psycopg2.errors.QueryCanceled: canceling statement due to statement timeout
File "/opt/render/project/src/frontend/dashboard/api.py", line 647, in route_tournaments
File "/opt/render/project/src/frontend/dashboard/api.py", line 987, in route_stats_categories
```

Ces erreurs viennent des appels USERS (Firefox qui rafraîchit la page) — pas du precompute lui-même. Le precompute, lui, n'a pas de statement_timeout (`set_statement_timeout("0")`).

---

## 6. Pistes pour la suite — par ordre de priorité

### 6.A — Optimiser les 2 requêtes SQL qui restent infinies
Même sans timeout, `/api/stats/categories` et `/api/tournaments` font des `GROUP BY` + `COUNT(DISTINCT)` sur 800k lignes. Solutions :

**Pour `/api/tournaments?limit=20`** (les 20 derniers tournois) :
La requête actuelle scan TOUS les tournois pour trouver `MIN(date)`. Réécrire en :
```sql
-- Stratégie 1 : limiter par id_tournoi DESC (si IDs ~ chronologique)
WITH recents AS (SELECT id_tournoi FROM tournois ORDER BY id_tournoi DESC LIMIT 200)
SELECT ... FROM tournois t JOIN recents r ON ... JOIN participations ...

-- Stratégie 2 : créer une table tournois_summary (id_tournoi, date_min, nb_joueurs)
-- alimentée par precompute.py — lecture instantanée ensuite
```

**Pour `/api/stats/categories`** :
Le subquery `(SELECT id_tournoi, COUNT(DISTINCT id_joueur) FROM participations GROUP BY id_tournoi)` scanne tout. Même approche : table matérialisée alimentée par precompute.

### 6.B — Bug "recherche n'affiche que des femmes"
À investiguer dans `dashboard_mockup.html` (probablement un filtre `sexe=F` par défaut dans le HTML), ou côté `search_players()` dans `player_profile.py` (ligne 27).

### 6.C — Recherche `/api/search` lente (~5-8s)
La query fait `LIKE '%q%'` (avec `%` devant) → force seq scan. Solutions :
- Activer extension `pg_trgm` + créer index trigram sur `nom`, `prenom`
- Ou changer en `LIKE 'q%'` (recherche par préfixe seulement)

### 6.D — Graph engine plante aussi
`graph_engine.py:_ensure_loaded()` charge un gros graphe de partenaires au boot → timeout aussi. Soit pré-charger ce graphe dans `cache_responses`, soit le rendre lazy (chargement au 1er hit `/api/ego`).

### 6.E — Solution radicale : upgrade Postgres Render Starter (~7$/mois)
Si on n'arrive pas à optimiser suffisamment, l'upgrade donne 1 GB RAM + CPU dédié → toutes les queries actuelles passent en 5-10x plus rapide sans changer une ligne de code.

---

## 7. Fichiers modifiés

| Fichier | Changements clés |
|---|---|
| `frontend/dashboard/db.py` | Index PG auto, table `cache_responses`, helpers `get_cached_body`/`set_cached_body`, `statement_timeout` configurable |
| `frontend/dashboard/api.py` | Routes lisent `cache_responses` via `_try_precomputed`, suppression du `_preheat_caches`, endpoints admin `/api/admin/precompute` et `/api/admin/precompute/status`, logs `[stats] counts: Xs` instrumentés |
| `frontend/dashboard/precompute.py` | NOUVEAU — script qui calcule les 5 réponses lourdes et UPSERT dans `cache_responses` |
| `frontend/dashboard_mockup.html` | `apiFetch` robustifié (AbortController, retry, parsing safe) |

## 8. Config Render

`render.yaml` (actuel) :
```yaml
buildCommand: pip install -r frontend/dashboard/requirements.txt
startCommand: cd frontend/dashboard && gunicorn api:app \
              --worker-class gthread --workers 1 --threads 4 \
              --timeout 120 --bind 0.0.0.0:$PORT
envVars:
  - DATABASE_URL  (auto, depuis padel-db)
  - ADMIN_KEY     (à configurer manuellement, valeur libre)
  - FLASK_ENV=production
  - PYTHON_VERSION=3.11.0   # mais Render utilise en fait Python 3.14.3
```

## 9. Comment reprendre

1. Cloner / ouvrir `G:\tenup_scraper\tenup_scraper_v2\`
2. Donner ce document au nouveau chat
3. Demander : "regarde les logs Render et identifie pourquoi `/api/tournaments` et `/api/stats/categories` ne finissent pas même avec timeout illimité (precompute)"
4. Priorité 1 : faire passer les 5 jobs precompute en ✅
5. Priorité 2 : matérialiser `tournois_summary` pour rendre les routes instantanées
6. Priorité 3 : fixer la recherche et le bug "que des femmes"

---

## 10. Workflow Git — règle absolue

**Ne jamais pousser directement sur `main` sauf hotfix d'urgence.**

### Workflow normal

```powershell
# 1. Travailler sur dev
git checkout dev
# ... faire les modifications ...
git add <fichiers modifiés>
git commit -m "feat: description"
git push origin dev
```

```powershell
# 2. Tester sur l'environnement dev
# → https://padel-stats-dev.onrender.com
# Si tout fonctionne → merger sur main
```

```powershell
# 3. Merger sur main (= déploiement prod)
git checkout main
git merge dev
git push origin main
# → Render redéploie automatiquement padel-stats-prod
```

```powershell
# 4. Resynchroniser dev après le merge
git checkout dev
git merge main
git push origin dev
```

### Commandes une par une (PowerShell ne supporte pas &&)

PowerShell n'accepte pas `cmd1 && cmd2`. Toujours lancer chaque commande séparément.

### Piège HEAD.lock

Le sandbox Claude crée parfois un fichier `.git/HEAD.lock` qui bloque les commits Windows.  
Si `git commit` échoue avec "Unable to create HEAD.lock" :

```powershell
Remove-Item G:\tenup_scraper\tenup_scraper_v2\.git\HEAD.lock -Force
```

### Piège CRLF

Windows utilise CRLF, Linux LF. Le warning `LF will be replaced by CRLF` est normal.  
Ne jamais laisser Claude faire `git add` + `git commit` depuis le sandbox sur des fichiers volumineux — risque de réécriture complète du fichier avec mauvais encodage et troncature.  
**Les commits git = toujours depuis le terminal Windows.**

### Structure Render

| Service | Branche | URL |
|---------|---------|-----|
| `padel-stats-prod` | `main` | https://padel-stats-oava.onrender.com |
| `padel-stats-dev` | `dev` | https://padel-stats-dev.onrender.com |

Root Directory des deux services : `frontend`  
Start command : `cd dashboard && gunicorn api:app --workers 1 --timeout 300 --preload --bind 0.0.0.0:$PORT`  
Variable d'env indispensable : `DATABASE_URL` (connection string PostgreSQL `padel-db`)

---

*Document mis à jour le 19 mai 2026.*
