# Phase 2 — Retravail de la donnée & front (passation)

> Suite de `DOC_SCRAPER_JSON.md` (le scraping est terminé et fonctionne).
> Ce document sert à **reprendre le travail avec un autre agent** : état actuel, ce qui est fait, et la feuille de route.

---

## État actuel (juin 2026)

- **Données** : backfill JSON réussi → ~169 500 bilans (99,7 % des classés padel), 1,38 M participations, validées (100 % partenaires, 0 doublon, règle « 12 meilleures perfs » OK, points réconciliés). Voir `DOC_SCRAPER_JSON.md`.
- **Base** : `backend/tenup.db` (SQLite). ⚠️ Gonflée (~775 Mo) → lancer `python cleanup_db.py --apply --vacuum` pour la ramener ~250 Mo. En prod, bascule possible vers PostgreSQL (`frontend/dashboard/db.py` gère les deux via `DATABASE_URL`).
- **Front** : `frontend/dashboard/` est l'app réelle (Flask + moteurs). `frontend/app.py` (Playwright) est l'**ancien** front lié au scraping HTML → **legacy, à ignorer/archiver**.

---

## ⚠️ Dépendance critique : le matching des partenaires

`frontend/dashboard/suggester.py` et `graph_engine.py` construisent le graphe de co-participation à partir de **`participations.partenaire_id`**. Or le scraper JSON ne remplit que `partenaire_nom` (nom+prénom) et laisse **`partenaire_id` vide**. → **Le graphe, le suggesteur et les degrés de séparation sont actuellement à vide.**

**Débloquer en premier** : `backend/match_partenaires.py` (déjà écrit, logique testée) résout `partenaire_nom` → `partenaire_id` (id_fft).
- Méthode : nom normalisé (majuscules, sans accents) + matching dans le bon pool H/F déduit du type d'épreuve (DM→H, DD→F, DX→sexe opposé). N'assigne que si le candidat est unique (sinon homonyme ambigu, laissé vide).
- Crée aussi l'index `idx_part_partenaire_id`.

```bash
python match_partenaires.py            # dry-run : taux matchés / ambigus / introuvables
python match_partenaires.py --apply     # écrit les partenaire_id
```
(N'a pas pu être lancé en live depuis l'agent — la grosse DB déclenche des I/O errors via le montage sandbox. À exécuter sous Windows.)

**Résultat réel (juin 2026)** : ~90 % matchés, 148k nœuds dans le graphe. Le ~10 % restant se décompose en :
- **Partenaires anonymisés (RGPD)** : ~78k participations avec `partenaire_nom` vide/"None None" + "Joueur Anonyme", et ~1 924 joueurs au nom NULL dans `joueurs` (tranche non classée, rang ~112546). Inhérent à la FFT, non matchable par nom. (Le scraper stockait "None None" par bug → corrigé ; `cleanup_db.py` vide ces valeurs.)
- **Homonymes** : noms français courants (ex. "Thomas MARTIN" → 18 candidats). Récupérables en **désambiguïsant par club** (`joueurs.club_nom`), par ligue/comité, ou par co-occurrence dans le graphe — piste d'amélioration pour le prochain agent.

---

## Feuille de route conseillée

1. **Nettoyer/compacter** : `python cleanup_db.py --apply --vacuum`.
2. **Matcher les partenaires** : `python match_partenaires.py --apply` (puis vérifier le taux). → débloque graphe + suggesteur.
3. **Recharger le graphe** : `frontend/dashboard/graph_engine.py` (`engine.load()`) reconstruit le graphe en mémoire depuis `participations.partenaire_id`. Vérifier que les BFS / degrés de séparation / suggestions fonctionnent.
4. **Mettre à jour les précalculs** : `frontend/dashboard/precompute.py` + `data_builder.py` génèrent les JSON statiques dans `frontend/dashboard/data/` (leaderboard, stats_globales, top_progressions, distribution_classements, top_clubs, top_villes, pyramide_ages, saisonnalite, hall_of_fame, bareme_points) + la table `tournois_summary`. À enrichir avec les **nouvelles données** :
   - **`rangs_pyramide`** (nouvelle table) → classements club/comité/ligue/national directs + **percentile sans recalcul**.
   - `classements_historique` (8 mois d'historique) → top progressions 1/3/12 mois, courbes d'évolution.
   - Colonnes `joueurs` enrichies : `points`, `ligue`, `comite`, `nationalite`, `niveau_galaxie`, `actif`, etc.
   - `participations.pris_en_compte` → distinguer perfs comptabilisées (12 meilleures) du reste.
4bis. **Poids des tournois** : `python tournois_stats.py --show` construit la table `tournois_stats` (niveau P normalisé 25→2000, flag par équipes, nb joueurs, classement moyen/meilleur des participants = force réelle du tableau, points distribués). Base pour des stats par niveau de tournoi. Note : les **joueurs anonymes (RGPD)** ont leur vrai classement (rangs 10→120486) → exploitables pour les stats de niveau, seul leur nom est masqué. nb_joueurs ≈ participants scrapés (très bonne couverture, pas un recensement parfait).
4ter. **Géographie** : `python build_geo.py --show` →
   - `joueurs.dept_num` : numéro de département (01..95, 2A/2B, 971..988) mappé depuis `comite` (pour cartes choroplèthes). Région = `ligue` (100%), département = `comite` (100%), ville = `joueurs.ville` (~67%).
   - Table `clubs` enrichie (ville/comite/ligue/dept_num depuis les membres).
   - Tables d'agrégats **H/F séparés** : `stats_geo_region`, `stats_geo_departement`, `stats_geo_ville`, `stats_geo_club` (nb joueurs H/F, nb clubs, classement moyen, meilleur).
   - Carte : choroplèthe par département = `dept_num` + un GeoJSON départements FR standard (côté front, rien à scraper).
4quater. **Géocodage villes (marqueurs carte)** : `python geocode_villes.py` (après build_geo, nécessite Internet) → récupère les communes FR via l'API officielle `geo.api.gouv.fr` (cache `communes_geo.json`), matche par nom+département, crée la table `villes_geo(ville, dept_num, lat, lon)` et ajoute `clubs.lat`/`clubs.lon`. Marqueurs de carte : `clubs(lat,lon)` ou `villes_geo`.
5. **Front** : polir `frontend/dashboard/` (Flask + HTML/Tailwind/Chart.js/Leaflet) ou moderniser (React/Vite consommant l'API). Features à valoriser : classements régionaux (pyramide), graphe partenaires + degrés de séparation, top progressions, fiche joueur (trophy shelf + percentile), vue tournoi (podium, paires). Servir les agrégats via JSON statiques (perf), le dynamique (recherche, fiche, BFS) via l'API.

---

## Règles & pièges à respecter

- **Jamais mélanger H et F** (règle d'or) — partout : classements, stats, graphe, suggestions.
- Clé joueur = `id_fft` (= idCrm). `classements_historique` utilise `id_joueur` (même valeur). `participations.id_joueur` et `partenaire_id` = id_fft.
- `db.py` lit `backend/tenup.db` en local (chemin `../../backend/tenup.db`) ou PostgreSQL si `DATABASE_URL` est défini.
- Déploiement actuel : VPS Hetzner + Docker + Cloudflare Tunnel (voir `RECAP_DEPLOIEMENT_RENDER.md` / `README.md`).

---

## Fichiers utiles (backend/)

- `match_partenaires.py` — résolution des partenaires (à lancer en premier).
- `tournois_stats.py` — poids/force de chaque tournoi (table `tournois_stats`).
- `build_geo.py` — couche géo : dept_num, clubs enrichis, tables `stats_geo_*` (région/département/ville/club).
- `geocode_villes.py` — coordonnées lat/lon des villes (table `villes_geo` + clubs.lat/lon) via geo.api.gouv.fr.
- `cleanup_db.py` — nettoyage + VACUUM.
- `validate_data.py`, `check_known.py` — contrôle qualité.
- `scraper_json.py`, `run_monthly.ps1` — le pipeline (phase 1, terminé).
- Utilitaires data conservés : `enrich_clubs.py`, `create_clubs.py`, `init_clubs.py`, `build_villes_ref.py`, `export_villes.py`, `generate_graph.py`, `points_tables.py`, `archive_inactifs.py`, `download_classement_csv.py`, `import_*.py`.
- `backend/archive/` : anciens scripts (dont `enrich_partenaires_local.py`, base existante du matching partenaires à comparer) + vieilles DB.
