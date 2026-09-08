# Note pour reprise du chantier "pipeline de donnees" (Padel Stats) - 08/09/2026

## Contexte
Suite a un signalement d'un tournoi ou toutes les paires affichaient "8e" (id 82546271), plusieurs chantiers ont ete ouverts sur `tenup_scraper_v2/backend` (scraper_json.py, cleanup_db.py) et `frontend/dashboard/api.py`.

## 1) Bug date_tournoi - VERDICT TROUVE le 08/09/2026 : la FFT NE FOURNIT PAS cette date dans le bilan

- Dans `scraper_json.py`, fonction `save_bilan()` : la colonne `date_tournoi` de `participations` est ecrite en dur a `None` a chaque INSERT, jamais backfillee (pas dans le `ON CONFLICT DO UPDATE`).
- Avant la bascule vers le scraper JSON, l'ancien scraper HTML (`archive/scraper_http.py`) parsait une vraie date depuis le tableau HTML rendu cote serveur. En DB, la derniere date reelle est mars 2026, plus rien apres - coincide avec la bascule HTML -> JSON.
- **Verification directe faite le 08/09/2026** : recupere le JSON complet de `/back/v1/personnes/{id}/bilan?discipline=PADEL` via `backend/dump_bilan.py` (nouveau script, appelle l'API en HTTP direct avec `cookie_header.txt`, contourne le navigateur/SPA). Exemple reel d'une entree `competitions[]` :

```json
{"dateFinPEC": "2026-10-15T00:00:00", "id": 82568902, "libelle": "P100 journee TC Les Lilas",
 "natureEpreuve": {...}, "typeEpreuve": {...}, "partenaire": {...},
 "rangPaire": 5, "pointsMarques": 35.0, "prisEnCompte": true}
```

**Il n'y a aucun champ de date du tournoi dans cet objet.** Seul `dateFinPEC` existe (date de fin de prise en compte des points, ~12 mois apres le tournoi) - c'est exactement la colonne `expiration` deja en base, rien de nouveau a extraire. Le champ n'a donc jamais ete "oublie" par le developpement du scraper JSON : il n'existe simplement pas a cet endroit de l'API FFT.

**Consequence pour la suite** : pas de fix simple possible via ce endpoint. Deux options si on veut un jour la date exacte :
1. Chercher un endpoint FFT different, au niveau du TOURNOI lui-meme (pas du bilan joueur) qui donnerait sa date - piste non exploree, `id_tournoi` est connu (ex: 82568902), il faudrait un endpoint type "fiche tournoi". A investiguer si ca vaut le coup.
2. Se contenter de l'estimation deja en place (`expiration - ~12 mois`, precision empirique +/-2 semaines, largement suffisante pour les usages actuels au mois pres : "Activite mensuelle", statut actif/en_pause/en_retraite a 3/12 mois).

**Decision actuelle : on reste sur l'estimation (option 2) sauf si quelqu'un retrouve un autre endpoint FFT avec la vraie date.**

`premiere_vue_mois` (ajoutee le 08/09/2026 dans `participations`) reste utile independamment : elle donne le mois de scraping reel pour toute nouvelle participation a partir de maintenant, sans dependre de cette histoire de champ FFT manquant.

Outils crees pendant l'investigation, a garder :
- `backend/dump_bilan.py` : dump le JSON complet du bilan d'un joueur dans un fichier (`bilan_<id>.json`) - utile pour toute future investigation sur cette API.
- `backend/archive/capture_api.py` : corrige (BASE_DIR pointait au mauvais endroit, lisait cookies.json perime au lieu de cookie_header.txt) - capture les appels /back/ via un navigateur Playwright pilote, utile si on veut explorer les pages qui NE sont PAS de simples endpoints HTTP. Note : `/classement/{id}/padel` et `/joueur/{id}` bouclent en redirection (ERR_TOO_MANY_REDIRECTS) meme en navigation manuelle - possible lien avec le fait que le compte n'est pas licencie pour la saison en cours ("licencie": false dans userStore), pas verifie.

## 2) Erosion des participations - FAIT le 08/09/2026

- `classements_historique` (classement mensuel par joueur) : purement additif, confirme par comparaison de backups. Rien touche ici.
- `participations` : erodait reellement chaque mois via `cleanup_db.py` etape 2 ("vieilles participations non rafraichies"), qui supprimait TOUTES les participations `pris_en_compte IS NULL` (signature ancien scraper HTML) des qu'un joueur etait re-scrape ce mois-ci - meme quand le tournoi etait juste trop vieux pour reapparaitre dans son bilan JSON. Chiffres releves avant fix : participations avec date reelle 949 268 (11/06) -> 808 529 (08/09) ; "ancien systeme" 133 540 (25/08) -> 38 840 (08/09), ~94 700 lignes purgees en un seul cycle.
- **Decision : on arrete l'erosion.** `cleanup_db.py` modifie (08/09/2026) : l'etape 2 ne supprime plus rien, juste comptee/affichee pour info. Les etapes 1 (orphelines), 3 (nettoyage "None None") et 4 (vrais doublons ancien/nouveau systeme identiques) sont inchangees.
- Le meilleur exemplaire encore complet du "avant erosion" reste le backup `tenup_backup_20260611_174840.db` (949 268 participations, 100% datees), au cas ou on voudrait restaurer ce qui a deja ete perdu entre juin et sept 2026.

## 3) Statut actif / en retraite - FAIT le 08/09/2026 (tourne sur estimation, cf point 1)

Regle :
- **"actif"** = a joue au moins un tournoi dans les 3 derniers mois.
- **"en_pause"** = zone grise 3-12 mois (nom provisoire).
- **"en_retraite"** = a deja joue au moins un tournoi, mais plus rien depuis 12 mois.
- **"jamais_joue"** = aucune date exploitable trouvee.
- Aucun joueur n'est jamais supprime, quel que soit son statut - juste un champ calcule.

Implemente dans `backend/compute_activite.py` (nouveau script, independant de `scraper_json.py`). Ajoute 3 colonnes a `joueurs` : `statut_activite`, `dernier_tournoi_date`, `dernier_tournoi_estime` (1 si estime, 0 si vraie date). Lance avec `--apply` le 08/09/2026, distribution obtenue :
- actif : 100 552 (100% bases sur l'estimation)
- en_pause : 71 992
- en_retraite : 19 216
- jamais_joue : 7 309

**Reste a faire** : ajouter l'appel a `compute_activite.py --apply` dans `run_monthly.ps1` / `run_monthly_full.ps1` pour que le statut se remette a jour automatiquement chaque mois.

## 4) Efficacite des membres (`/api/club_scatter`)

Pas touche pour l'instant. Pistes en attente : ratio points/tournoi, fenetre 12 mois plutot que all-time, tendance dans le temps, ponderation par indice de difficulte, comparaison a la moyenne du club/departement.

## Fichiers modifies/crees le 08/09/2026

- `backend/cleanup_db.py` - modifie (erosion stoppee)
- `backend/scraper_json.py` - modifie (colonne `premiere_vue_mois` ajoutee)
- `backend/compute_activite.py` - nouveau
- `backend/dump_bilan.py` - nouveau (outil d'investigation, a garder)
- `backend/archive/capture_api.py` - modifie (fix BASE_DIR + cookie_header.txt + pause manuelle)

Schema/donnees deja appliques directement sur `tenup.db` en local (colonnes ajoutees, `compute_activite.py --apply` deja execute) - independant du push git. Pas besoin de deploiement VPS pour ces changements (voir section "Depot Git et deploiement" du projet - le VPS ne sert que le frontend, ces scripts tournent en local).
