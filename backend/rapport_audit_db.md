# Rapport d'audit & nettoyage — tenup.db
**Date :** 17 mai 2026  
**Base principale :** `tenup.db` (329 MB, WAL mode)

---

## 1. Structure des bases

| Fichier | Tables | État |
|---------|--------|------|
| `tenup.db` | 11 | ✅ Base principale, auditée et nettoyée |
| `padel.db` | 0 | ⚠️ Vide (aucune table) |
| `tenup_test.db` | 5 | ℹ️ Base de test, ~10 joueurs |

---

## 2. État des données — tenup.db

### Table `joueurs` (156 407 enregistrements)

| Colonne | Nulls / Vides | % | Remarque |
|---------|--------------|---|----------|
| `id_fft` | 0 | 0% | ✅ Clé primaire intègre, aucun doublon |
| `nom` | 0 | 0% | ✅ |
| `prenom` | 1 500 | 1% | ⚠️ Données manquantes source |
| `ville` | 13 333 | 8.5% | ⚠️ Données manquantes source |
| `echelon` | 1 233 | 0.8% | ⚠️ Données manquantes source |
| `naissance` | 13 378 | 8.6% | ⚠️ Données manquantes source |
| `classement` | 5 316 | 3.4% | ⚠️ Joueurs non classés |
| `niveau` | 156 407 | **100%** | 🔴 Colonne toujours vide — à supprimer ou alimenter |
| `club_nom` | 1 758 | 1.1% | ⚠️ Données manquantes source |
| `sexe` | 0 | 0% | ✅ Valeurs : H (145 543) / F (10 864) |

### Table `participations` (1 032 710 enregistrements)

- ✅ **Aucune participation orpheline** : toutes les FK `id_joueur` et `id_tournoi` sont valides
- ✅ **Points cohérents** : min=0, max=98, moyenne=53.8, aucun négatif
- ✅ **Types** : DM (897 476) / DX (81 461) / DD (53 773)
- ✅ **Positions** : toutes renseignées

### Table `classements_historique` (275 978 enregistrements)

- ✅ **Aucun doublon** (joueur + mois)
- ✅ **Classements cohérents** : min=1, max=117 387
- ℹ️ Historique couvre **2 mois** : 2026-04 et 2026-05 (historique encore limité)

### Table `tournois` (37 880 enregistrements)

- ✅ Catégories cohérentes (P25, P100, P250, P500, P1000, P1500…)
- ℹ️ Quelques libellés longs (ex. "Championnat Départemental (P250)") — à harmoniser si nécessaire

---

## 3. Nettoyages appliqués

### 3.1 Normalisation de la casse des villes

**Problème :** 2 558 villes étaient en casse mixte (`Paris`, `Marseille`, `Lyon`, `Saint-Denis`…) alors que la majorité était en MAJUSCULES.

**Action :** `UPPER(TRIM(ville))` appliqué sur l'ensemble de la table.

**Impact :** 6 583 joueurs mis à jour. Exemple : `'Paris'` → `'PARIS'` (741 joueurs).

### 3.2 Normalisation des abréviations ST / SAINT

**Problème :** 514 groupes de doublons où la même commune apparaissait sous plusieurs formes :
- `'ST MALO'` (91) + `'SAINT-MALO'` (34)  
- `'ST MARTIN'` (245) + `'SAINT MARTIN'` (94)  
- `'ST DENIS'` (287) + `'SAINT-DENIS'` (64)  
- etc.

**Action :** Toutes les formes `ST X`, `SAINTE X`, `SAINT X` converties vers `SAINT-X` / `SAINTE-X` (avec tiret, forme officielle française).

**Impact :** 11 925 joueurs mis à jour, **329 doublons de villes fusionnés**.

| Avant | Après |
|-------|-------|
| 17 126 villes distinctes | **16 796 villes distinctes** |

Exemples de fusions :
| Avant (fragmenté) | Après (unifié) |
|-------------------|----------------|
| `'ST MALO'`(91) + `'SAINT-MALO'`(34) | `'SAINT-MALO'`(125) |
| `'ST MARTIN'`(245) + `'SAINT MARTIN'`(94) | `'SAINT-MARTIN'`(339) |
| `'ST DENIS'`(287) + `'SAINT-DENIS'`(64) | `'SAINT-DENIS'`(351) |
| `'ST LAURENT DU VAR'`(177) + `'SAINT-LAURENT-DU-VAR'`(43) | `'SAINT-LAURENT-DU-VAR'`(220) |

### 3.3 Backup créé avant modification

`tenup_clean_backup_20260516_224922.db` — permet de revenir à l'état précédent si nécessaire.

---

## 4. Points à surveiller (non corrigés automatiquement)

| Sujet | Détail | Recommandation |
|-------|--------|----------------|
| `niveau` (colonne vide) | 100% null sur 156k joueurs | Alimenter via l'API ou supprimer la colonne |
| Villes manquantes | 13 333 joueurs sans ville (8.5%) | Problème source (scraper) — pas corrigeable en base |
| Naissances manquantes | 13 378 (8.6%) | Idem |
| `padel.db` vide | Aucune table | À supprimer ou à initialiser |
| Historique limité | Seulement 2 mois (avr-mai 2026) | Normal si le scraping mensuel est récent |
| Villes étrangères | Madrid, Mouscron, Nouméa… | Cohérent pour un registre FFT international |

---

## 5. Résumé chiffré

| Métrique | Valeur |
|----------|--------|
| Joueurs traités | 156 407 |
| Joueurs avec ville normalisée | 18 176 |
| Villes dédupliquées | 329 |
| Villes distinctes finales | 16 796 |
| Intégrité FK participations → joueurs | ✅ 0 orphelin |
| Intégrité FK participations → tournois | ✅ 0 orphelin |
| Doublons id_fft | ✅ 0 |
| Doublons classement (joueur+mois) | ✅ 0 |
