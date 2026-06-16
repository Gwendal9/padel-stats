# Notation de la difficulté des tournois — méthodologie

> Objectif : attribuer à chaque tournoi un **indice de niveau réel** qui reflète la force du plateau,
> et pas seulement son label P (P25…P2000). Deux P100 peuvent être très différents.

## 1. Le problème avec la moyenne brute

Deux pièges si on note un tournoi par la *moyenne des classements* des participants :

1. **Le classement n'est pas linéaire.** Un 2000e est *énormément* plus fort qu'un 40000e ;
   l'écart de niveau entre le 2000e et le 22000e est bien plus grand que le simple écart de rangs
   ne le laisse croire. Le rang est ordinal, le niveau est ~logarithmique.

2. **La moyenne écrase la distribution.** Une paire 2000 + 40000 et une paire 22000 + 22000 ont
   la même *moyenne de rang* (≈ 21000) alors qu'elles n'ont pas le même niveau : la première a un
   joueur d'élite qui « tire » la paire.

```
Paire A : 2000  + 40000   → moyenne brute des rangs = 21 000
Paire B : 22000 + 22000   → moyenne brute des rangs = 22 000
→ quasi identiques en moyenne brute. C'est le défaut à corriger.
```

## 2. La correction : passer du rang à un score de niveau

On transforme chaque rang `r` (dans le **bon pool H/F** — règle d'or) en un score de niveau
quasi linéaire :

```
s = ln(N / r)            # N = taille du pool (≈ 155 820 chez les H)
```

`s` est élevé pour les forts (rang petit), faible pour les autres. La différence entre deux joueurs
devient proportionnelle à l'écart de *niveau*, pas de *rang*.

```
s(2000)  = ln(155820/2000)  = 4,36
s(22000) = ln(155820/22000) = 1,96
s(40000) = ln(155820/40000) = 1,36
```

On peut le normaliser en 0–100 pour l'affichage : `s_norm = 100 · ln(N/r) / ln(N)`
(rang 1 ≈ 100, dernier rang ≈ 0).

## 3. Niveau d'une paire (pondéré vers le plus fort)

Pour une paire dont les scores sont `s_max ≥ s_min` :

```
paire = w · s_max + (1 − w) · s_min      avec w ≥ 0,5  (défaut w = 0,6)
```

`w = 0,5` = moyenne des log-rangs (= moyenne géométrique des rangs) : déjà bien meilleure que la
moyenne brute. `w > 0,5` accentue le fait qu'un joueur fort soulève la paire.

```
Paire A (2000,40000) : 0,6·4,36 + 0,4·1,36 = 3,16
Paire B (22000,22000): 0,6·1,96 + 0,4·1,96 = 1,96
→ A nettement au-dessus de B (3,16 vs 1,96), alors que la moyenne brute les disait égales.  ✅
```

C'est exactement le comportement que tu veux.

## 4. Force du plateau d'un tournoi

On agrège les niveaux de paires en regardant la **distribution**, pas juste la moyenne :

- **Plafond** `C` = moyenne des meilleures paires (top 25 %, ou top 4) → à quel point c'est dur de
  *gagner* le tournoi.
- **Profondeur** `D` = médiane des paires → niveau général du tableau.
- **Force du plateau** : `F = β·C + (1−β)·D`   (défaut β = 0,6, pondéré plafond, car la difficulté
  de gagner dépend surtout du haut de tableau).

On garde aussi `C` et `D` séparément : un tournoi peut être « 2 grosses paires + du tout-venant »
(plafond haut, profondeur basse) ou « plateau homogène et relevé » (les deux hauts).

> Paires reconstruites via `participations.partenaire_id`. Quand le partenaire est anonyme (RGPD,
> `partenaire_id` vide), on retombe sur le rang du joueur connu seul (estimation 1 côté). Les
> joueurs anonymes gardent leur vrai rang → exploitables pour le niveau.

## 5. Indice final + surcote par rapport au label P

Deux chiffres complémentaires par tournoi :

1. **`indice_niveau` (0–100, absolu)** : dérivé de `F`. Permet de comparer *n'importe quels*
   tournois entre eux (un P100 peut dépasser un P500 faible).
2. **`surcote_niveau`** : `F` comparé à la distribution des `F` des tournois **du même label P**
   (z-score ou percentile intra-niveau). Répond à « ce P100 est-il relevé pour un P100 ? ».
   Ex. « +1,8 σ → top 5 % des P100 ».

Le label P (P25…P2000) reste affiché comme **niveau nominal** (dotation/format FFT) à côté de
l'indice de niveau réel.

## 6. Dimension format / engagement (matchs, terrains, attente)

C'est une **autre dimension** que le niveau du plateau, et surtout : **ces données ne sont pas dans
le scrape actuel** (le `bilan` FFT donne résultats + partenaires + points, pas le nombre de matchs,
de terrains, ni les horaires).

Ce qu'on peut estimer depuis l'existant :
- **Nombre de paires** = participations du tournoi (ou `nb_joueurs/2`).
- **Tours de tableau** ≈ `ceil(log2(nb_paires))` → nombre de matchs d'un finaliste en élimination
  directe ; nb de matchs total ≈ `nb_paires − 1` (élim. directe). ⚠️ approximation : on ne connaît
  pas le format réel (poules vs élim, consolante…), seulement les positions finales.

Ce qu'on **ne peut pas** déduire : nombre de terrains, temps d'attente entre matchs, durée. Il
faudrait une autre source (fiche tournoi de l'organisateur / page détail FFT non scrapée).

→ Proposition : commencer par l'**indice de niveau** (calculable, robuste, c'est le cœur de ta
demande), avec un emplacement prévu pour un **sous-score « format/engagement »** branché plus tard
si on trouve la source des terrains/horaires. La note finale pourrait être :

```
note_globale = α · indice_niveau + γ · indice_format      (γ = 0 tant qu'on n'a pas la donnée format)
```

## 7. Paramètres à calibrer (sur tes vraies données)

- `w` (pondération vers le plus fort dans une paire) — défaut 0,6.
- `β` (plafond vs profondeur) — défaut 0,6.
- top-k pour le plafond (top 25 % ou top 4 paires).
- mapping `F → indice 0–100` (à caler sur la distribution réelle, ex. percentiles).

Ces réglages se calibrent en regardant la distribution réelle des `F` par niveau P — à faire au
premier passage du script sur ta machine.

## 8. Sortie technique

Script `backend/tournois_rating.py` → table `tournois_rating` :
`id_tournoi, niveau_points, nb_paires, skill_plafond, skill_profondeur, force_plateau,
indice_niveau, surcote_niveau`. À lancer sous Windows (écrit la base, base volumineuse).
Surface ensuite dans la vue tournoi et pour **pondérer les performances** d'un joueur (battre une
grosse paire dans un P100 relevé > gagner un P100 faible).
