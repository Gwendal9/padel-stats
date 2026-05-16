"""
Tableaux de répartition des points par catégorie de tournoi (FFT padel).

Source : barème officiel FFT.
Lecture : POINTS[categorie][rang] = liste de points selon le nombre de paires.

Les colonnes (= taille du tournoi) :
    BRACKETS_LABELS = ['4-8','9-12','13-16','17-20','21-24','25-28','29-32+']
Pour P1500 (et plus haut), seulement les 4 dernières colonnes sont valides.
"""

BRACKETS_LABELS = ['4-8', '9-12', '13-16', '17-20', '21-24', '25-28', '29-32+']
BRACKETS_RANGES = [(4, 8), (9, 12), (13, 16), (17, 20), (21, 24), (25, 28), (29, 999)]

def bracket_index(n_pairs):
    """Retourne l'index de colonne (0-6) pour un nombre de paires donné."""
    for i, (lo, hi) in enumerate(BRACKETS_RANGES):
        if lo <= n_pairs <= hi:
            return i
    return -1

# ─── P25 ──────────────────────────────────────────────────────────────
# Rang : [4-8, 9-12, 13-16, 17-20, 21-24, 25-28, 29-32+]
P25 = {
    1:  [25,25,25,25,25,25,25],
    2:  [15,17,18,20,20,21,23],
    3:  [12,15,16,18,19,19,21],
    4:  [9,13,15,17,18,18,19],
    5:  [6,11,14,16,17,17,18],
    6:  [4,9,13,15,16,16,17],
    7:  [2,7,12,14,15,15,16],
    8:  [1,5,11,13,14,14,15],
    9:  [None,4,10,12,13,13,14],
    10: [None,3,9,11,12,12,13],
    11: [None,2,7,10,11,11,12],
    12: [None,1,5,9,10,10,11],
    13: [None,None,4,8,9,9,10],
    14: [None,None,3,7,8,8,9],
    15: [None,None,2,6,7,7,8],
    16: [None,None,1,5,6,6,7],
    17: [None,None,None,4,5,5,6],
    18: [None,None,None,3,4,4,5],
    19: [None,None,None,2,3,3,4],
    20: [None,None,None,1,2,2,3],
    21: [None,None,None,None,1,1,2],
    22: [None,None,None,None,1,1,1],
    23: [None,None,None,None,1,1,1],
    24: [None,None,None,None,1,1,1],
    25: [None,None,None,None,None,1,1],
    26: [None,None,None,None,None,1,1],
    27: [None,None,None,None,None,1,1],
    28: [None,None,None,None,None,1,1],
    29: [None,None,None,None,None,None,1],
    30: [None,None,None,None,None,None,1],
    31: [None,None,None,None,None,None,1],
    32: [None,None,None,None,None,None,1],
}

# ─── P100 ─────────────────────────────────────────────────────────────
P100 = {
    1:  [100,100,100,100,100,100,100],
    2:  [60,65,70,75,75,80,80],
    3:  [50,55,60,65,70,75,75],
    4:  [40,50,55,60,65,70,72],
    5:  [25,35,45,55,60,65,70],
    6:  [20,25,40,50,55,60,65],
    7:  [5,20,35,45,50,55,63],
    8:  [1,15,30,40,47,53,60],
    9:  [None,10,25,35,43,50,58],
    10: [None,5,21,30,40,48,55],
    11: [None,3,18,25,37,45,53],
    12: [None,1,15,23,33,43,50],
    13: [None,None,10,20,30,40,48],
    14: [None,None,5,18,28,38,45],
    15: [None,None,3,15,25,35,43],
    16: [None,None,1,12,23,33,40],
    17: [None,None,None,10,20,30,38],
    18: [None,None,None,5,18,28,35],
    19: [None,None,None,3,15,25,33],
    20: [None,None,None,1,12,23,30],
    21: [None,None,None,None,10,20,28],
    22: [None,None,None,None,5,18,25],
    23: [None,None,None,None,3,15,23],
    24: [None,None,None,None,1,12,20],
    25: [None,None,None,None,None,10,18],
    26: [None,None,None,None,None,5,15],
    27: [None,None,None,None,None,3,12],
    28: [None,None,None,None,None,1,10],
    29: [None,None,None,None,None,None,8],
    30: [None,None,None,None,None,None,5],
    31: [None,None,None,None,None,None,3],
    32: [None,None,None,None,None,None,1],
}

# ─── P250 ─────────────────────────────────────────────────────────────
P250 = {
    1:  [250,250,250,250,250,250,250],
    2:  [150,163,175,188,188,200,200],
    3:  [125,138,150,163,175,188,188],
    4:  [100,125,138,150,163,175,180],
    5:  [63,88,113,138,150,163,175],
    6:  [25,63,100,125,138,150,163],
    7:  [13,50,88,113,125,138,158],
    8:  [3,38,75,100,118,133,150],
    9:  [None,25,63,88,108,125,145],
    10: [None,13,53,75,100,120,138],
    11: [None,8,45,63,93,113,133],
    12: [None,3,38,58,83,108,125],
    13: [None,None,25,50,75,100,120],
    14: [None,None,13,45,70,95,113],
    15: [None,None,8,38,63,88,108],
    16: [None,None,3,30,58,83,100],
    17: [None,None,None,25,50,75,95],
    18: [None,None,None,13,45,70,88],
    19: [None,None,None,8,38,63,83],
    20: [None,None,None,3,30,58,75],
    21: [None,None,None,None,25,50,70],
    22: [None,None,None,None,13,45,63],
    23: [None,None,None,None,8,38,58],
    24: [None,None,None,None,3,30,50],
    25: [None,None,None,None,None,25,45],
    26: [None,None,None,None,None,13,38],
    27: [None,None,None,None,None,8,30],
    28: [None,None,None,None,None,3,25],
    29: [None,None,None,None,None,None,20],
    30: [None,None,None,None,None,None,13],
    31: [None,None,None,None,None,None,8],
    32: [None,None,None,None,None,None,3],
}

# ─── P500 ─────────────────────────────────────────────────────────────
P500 = {
    1:  [500,500,500,500,500,500,500],
    2:  [300,325,350,375,375,400,400],
    3:  [250,275,300,325,350,375,375],
    4:  [200,250,275,300,325,350,360],
    5:  [125,175,225,275,300,325,350],
    6:  [50,125,200,250,275,300,325],
    7:  [25,100,175,225,250,275,315],
    8:  [5,75,150,200,235,265,300],
    9:  [None,50,125,175,215,250,290],
    10: [None,25,105,150,200,240,275],
    11: [None,15,90,125,185,225,265],
    12: [None,5,75,115,165,215,250],
    13: [None,None,50,100,150,200,240],
    14: [None,None,25,90,140,190,225],
    15: [None,None,15,75,125,175,215],
    16: [None,None,5,60,115,165,200],
    17: [None,None,None,50,100,150,190],
    18: [None,None,None,25,90,140,175],
    19: [None,None,None,15,75,125,165],
    20: [None,None,None,5,60,115,150],
    21: [None,None,None,None,50,100,140],
    22: [None,None,None,None,25,90,125],
    23: [None,None,None,None,15,75,115],
    24: [None,None,None,None,5,60,100],
    25: [None,None,None,None,None,50,90],
    26: [None,None,None,None,None,25,75],
    27: [None,None,None,None,None,15,60],
    28: [None,None,None,None,None,5,50],
    29: [None,None,None,None,None,None,40],
    30: [None,None,None,None,None,None,25],
    31: [None,None,None,None,None,None,15],
    32: [None,None,None,None,None,None,5],
}

# ─── P1500 ─────────────────────────────────────────────────────────────
# Pour P1500, seules les 4 colonnes (16-20, 21-24, 25-28, 29-32+) sont valides
# (tournoi minimum de 16 paires requis).
P1500 = {
    1:  [None,None,None,None,1500,1500,1500],
    2:  [None,None,None,None,1125,1200,1200],
    3:  [None,None,None,None,1050,1125,1125],
    4:  [None,None,None,None,975,1050,1080],
    5:  [None,None,None,None,900,975,1050],
    6:  [None,None,None,None,825,900,975],
    7:  [None,None,None,None,750,825,945],
    8:  [None,None,None,None,705,795,900],
    9:  [None,None,None,None,645,750,870],
    10: [None,None,None,None,600,720,825],
    11: [None,None,None,None,555,675,795],
    12: [None,None,None,None,495,645,750],
    13: [None,None,None,None,450,600,720],
    14: [None,None,None,None,420,570,675],
    15: [None,None,None,None,375,525,645],
    16: [None,None,None,1500,345,495,600],
    17: [None,None,None,1125,300,450,570],
    18: [None,None,None,1050,270,420,525],
    19: [None,None,None,975,225,375,495],
    20: [None,None,None,900,180,345,450],
    21: [None,None,None,None,150,300,420],
    22: [None,None,None,None,75,270,375],
    23: [None,None,None,None,45,225,345],
    24: [None,None,None,None,15,180,300],
    25: [None,None,None,None,None,150,270],
    26: [None,None,None,None,None,75,225],
    27: [None,None,None,None,None,45,180],
    28: [None,None,None,None,None,15,150],
    29: [None,None,None,None,None,None,120],
    30: [None,None,None,None,None,None,75],
    31: [None,None,None,None,None,None,45],
    32: [None,None,None,None,None,None,15],
}

POINTS = {'P25': P25, 'P100': P100, 'P250': P250, 'P500': P500, 'P1500': P1500}

# ─── Détection : combien de tours dans un bracket selon le nb de paires ───
def bracket_structure(n_pairs):
    """
    Retourne la structure du bracket pour N paires.
    Format : [(round_name, pairs_starting), ...] de la 1ère phase à la finale.

    Cas typiques :
        4-8 paires      → 1/4 → 1/2 → finale
        9-16 paires     → 1/8 → 1/4 → 1/2 → finale (ou poules pour 9-15)
        17-32 paires    → 1/16 → 1/8 → 1/4 → 1/2 → finale
        33-64 paires    → 1/32 → ...
    """
    if n_pairs <= 4:
        return [('1/2 finale', 4), ('Finaliste', 2), ('Vainqueur', 1)]
    if n_pairs <= 8:
        return [('1/4 finale', 8), ('1/2 finale', 4), ('Finaliste', 2), ('Vainqueur', 1)]
    if n_pairs <= 16:
        return [('1/8 finale', 16), ('1/4 finale', 8), ('1/2 finale', 4),
                ('Finaliste', 2), ('Vainqueur', 1)]
    if n_pairs <= 32:
        return [('1/16 finale', 32), ('1/8 finale', 16), ('1/4 finale', 8),
                ('1/2 finale', 4), ('Finaliste', 2), ('Vainqueur', 1)]
    if n_pairs <= 64:
        return [('1/32 finale', 64), ('1/16 finale', 32), ('1/8 finale', 16),
                ('1/4 finale', 8), ('1/2 finale', 4), ('Finaliste', 2), ('Vainqueur', 1)]
    return [('Phase préliminaire', n_pairs), ('1/32 finale', 64),
            ('1/16 finale', 32), ('1/8 finale', 16), ('1/4 finale', 8),
            ('1/2 finale', 4), ('Finaliste', 2), ('Vainqueur', 1)]

def expected_points(category, n_pairs, rang):
    """Retourne les points attendus pour un rang dans un tournoi de catégorie X et N paires."""
    if category not in POINTS: return None
    idx = bracket_index(n_pairs)
    if idx < 0: return None
    table = POINTS[category]
    if rang not in table: return None
    return table[rang][idx]
