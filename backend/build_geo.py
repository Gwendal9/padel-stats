"""
build_geo.py — Couche géographique propre pour les stats (région / département / ville / club).

Ce que ça fait :
  1. Mappe chaque `comite` (zone FFT, = département) à son NUMÉRO de département
     (01..95, 2A/2B, 971..988) → colonne `joueurs.dept_num` (pour cartes choroplèthes).
  2. Enrichit la table `clubs` (ville / comite / ligue / dept_num) depuis ses membres.
  3. Construit des tables d'agrégats, H et F séparés :
        stats_geo_region, stats_geo_departement, stats_geo_ville, stats_geo_club

Géographie disponible (rappel) : `ligue` = région, `comite` = département (100% des classés),
`ville` ~67%. Les "Comité (L0) de la LIGUE X" = rattachements au niveau ligue (pas de
département précis) → dept_num NULL, mais la région reste exploitable.

Usage :
    python build_geo.py            # construit tout
    python build_geo.py --show     # + aperçu
"""
import sqlite3, sys, os, unicodedata
from collections import Counter, defaultdict

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tenup.db')
SHOW = '--show' in sys.argv


def norm(s):
    if not s: return ''
    s = unicodedata.normalize('NFKD', s)
    s = ''.join(c for c in s if not unicodedata.combining(c)).upper()
    for ch in "-'.": s = s.replace(ch, ' ')
    return ' '.join(s.split())


# Référence des départements français (num, nom propre)
_DEPTS = [
    ('01','Ain'),('02','Aisne'),('03','Allier'),('04','Alpes-de-Haute-Provence'),
    ('05','Hautes-Alpes'),('06','Alpes-Maritimes'),('07','Ardèche'),('08','Ardennes'),
    ('09','Ariège'),('10','Aube'),('11','Aude'),('12','Aveyron'),('13','Bouches-du-Rhône'),
    ('14','Calvados'),('15','Cantal'),('16','Charente'),('17','Charente-Maritime'),('18','Cher'),
    ('19','Corrèze'),('2A','Corse-du-Sud'),('2B','Haute-Corse'),('21',"Côte-d'Or"),
    ('22',"Côtes-d'Armor"),('23','Creuse'),('24','Dordogne'),('25','Doubs'),('26','Drôme'),
    ('27','Eure'),('28','Eure-et-Loir'),('29','Finistère'),('30','Gard'),('31','Haute-Garonne'),
    ('32','Gers'),('33','Gironde'),('34','Hérault'),('35','Ille-et-Vilaine'),('36','Indre'),
    ('37','Indre-et-Loire'),('38','Isère'),('39','Jura'),('40','Landes'),('41','Loir-et-Cher'),
    ('42','Loire'),('43','Haute-Loire'),('44','Loire-Atlantique'),('45','Loiret'),('46','Lot'),
    ('47','Lot-et-Garonne'),('48','Lozère'),('49','Maine-et-Loire'),('50','Manche'),('51','Marne'),
    ('52','Haute-Marne'),('53','Mayenne'),('54','Meurthe-et-Moselle'),('55','Meuse'),('56','Morbihan'),
    ('57','Moselle'),('58','Nièvre'),('59','Nord'),('60','Oise'),('61','Orne'),('62','Pas-de-Calais'),
    ('63','Puy-de-Dôme'),('64','Pyrénées-Atlantiques'),('65','Hautes-Pyrénées'),('66','Pyrénées-Orientales'),
    ('67','Bas-Rhin'),('68','Haut-Rhin'),('69','Rhône'),('70','Haute-Saône'),('71','Saône-et-Loire'),
    ('72','Sarthe'),('73','Savoie'),('74','Haute-Savoie'),('75','Paris'),('76','Seine-Maritime'),
    ('77','Seine-et-Marne'),('78','Yvelines'),('79','Deux-Sèvres'),('80','Somme'),('81','Tarn'),
    ('82','Tarn-et-Garonne'),('83','Var'),('84','Vaucluse'),('85','Vendée'),('86','Vienne'),
    ('87','Haute-Vienne'),('88','Vosges'),('89','Yonne'),('90','Territoire de Belfort'),('91','Essonne'),
    ('92','Hauts-de-Seine'),('93','Seine-Saint-Denis'),('94','Val-de-Marne'),("95","Val-d'Oise"),
    ('971','Guadeloupe'),('972','Martinique'),('973','Guyane'),('974','La Réunion'),
    ('976','Mayotte'),('988','Nouvelle-Calédonie'),
]
LOOKUP = {norm(nom): (num, nom) for num, nom in _DEPTS}
NUM2NOM = {num: nom for num, nom in _DEPTS}
# alias pour les libellés FFT qui diffèrent du nom officiel
ALIASES = {
    'SEINE ST DENIS': '93', 'RHONE METROPOLE DE LYON': '69', 'BELFORT': '90',
}
DOM = {'GUADELOUPE':'971','MARTINIQUE':'972','GUYANE':'973','REUNION':'974',
       'MAYOTTE':'976','NOUVELLE CALEDONIE':'988'}


def dept_of(comite):
    """comité (libellé FFT) -> (num_departement, nom_departement) ; (None,None) si niveau ligue."""
    n = norm(comite)
    if n.startswith('COMITE'):              # "Comité (L0) de la LIGUE X" = rattachement ligue
        for kw, num in DOM.items():
            if kw in n:
                return num, NUM2NOM[num]
        return None, None
    if n in LOOKUP:  return LOOKUP[n]
    if n in ALIASES: num = ALIASES[n]; return num, NUM2NOM[num]
    return None, None


def main():
    c = sqlite3.connect(DB, isolation_level=None)
    for mig in ["ALTER TABLE joueurs ADD COLUMN dept_num TEXT",
                "ALTER TABLE clubs ADD COLUMN comite TEXT",
                "ALTER TABLE clubs ADD COLUMN ligue TEXT",
                "ALTER TABLE clubs ADD COLUMN dept_num TEXT"]:
        try: c.execute(mig)
        except Exception: pass
    # index pour accélérer (sinon enrichissement clubs = des millions de scans)
    for idx in ["CREATE INDEX IF NOT EXISTS idx_j_comite ON joueurs(comite)",
                "CREATE INDEX IF NOT EXISTS idx_j_club_id ON joueurs(club_id)"]:
        try: c.execute(idx)
        except Exception: pass

    # 1) dept_num sur joueurs (par comité distinct)
    comites = [r[0] for r in c.execute("SELECT DISTINCT comite FROM joueurs WHERE comite!=''")]
    mapped = non = 0
    c.execute("BEGIN")
    for com in comites:
        num, _ = dept_of(com)
        if num: mapped += 1
        else:   non += 1
        c.execute("UPDATE joueurs SET dept_num=? WHERE comite=?", (num, com))
    c.execute("COMMIT")
    print(f"comités mappés : {mapped}/{len(comites)}  (non mappés / niveau ligue : {non})")

    # 2) enrichir clubs depuis les membres (valeur majoritaire) — en une seule passe
    print("Enrichissement des clubs depuis leurs membres…")
    agg = defaultdict(lambda: {'ville': Counter(), 'comite': Counter(),
                               'ligue': Counter(), 'dept_num': Counter()})
    for cid, ville, com, lig, dn in c.execute(
            "SELECT club_id, ville, comite, ligue, dept_num FROM joueurs WHERE club_id IS NOT NULL"):
        a = agg[cid]
        if ville: a['ville'][ville] += 1
        if com:   a['comite'][com] += 1
        if lig:   a['ligue'][lig] += 1
        if dn:    a['dept_num'][dn] += 1
    def top(cnt): return cnt.most_common(1)[0][0] if cnt else None
    updates = [(top(a['ville']), top(a['comite']), top(a['ligue']), top(a['dept_num']), cid)
               for cid, a in agg.items()]
    c.execute("BEGIN")
    c.executemany("UPDATE clubs SET ville=?, comite=?, ligue=?, dept_num=? WHERE id=?", updates)
    c.execute("COMMIT")
    print(f"  {len(updates):,} clubs enrichis")

    # 3) tables d'agrégats (H/F séparés)
    def build(table, group_cols, where_extra=""):
        cols_sel = ", ".join(group_cols)
        c.execute(f"DROP TABLE IF EXISTS {table}")
        c.execute(f"""CREATE TABLE {table} AS
            SELECT {cols_sel},
                   COUNT(*) AS nb_total,
                   SUM(CASE WHEN sexe='H' THEN 1 ELSE 0 END) AS nb_h,
                   SUM(CASE WHEN sexe='F' THEN 1 ELSE 0 END) AS nb_f,
                   COUNT(DISTINCT club_id) AS nb_clubs,
                   CAST(AVG(classement) AS INT) AS classement_moyen,
                   CAST(AVG(CASE WHEN sexe='H' THEN classement END) AS INT) AS classement_moyen_h,
                   CAST(AVG(CASE WHEN sexe='F' THEN classement END) AS INT) AS classement_moyen_f
            FROM joueurs
            WHERE dernier_mois_vu IS NOT NULL {where_extra}
            GROUP BY {cols_sel}""")
        n = c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table}: {n:,} lignes")

    print("Construction des agrégats géo…")
    build("stats_geo_region",       ["ligue"],                       "AND ligue!=''")
    build("stats_geo_departement",  ["dept_num", "comite", "ligue"], "AND dept_num IS NOT NULL")
    build("stats_geo_ville",        ["ville", "comite", "ligue"],    "AND ville!=''")
    # par club (avec sa géo enrichie)
    c.execute("DROP TABLE IF EXISTS stats_geo_club")
    c.execute("""CREATE TABLE stats_geo_club AS
        SELECT j.club_id, cl.nom AS club_nom, cl.ville, cl.comite, cl.dept_num, cl.ligue,
               COUNT(*) AS nb_membres,
               SUM(CASE WHEN j.sexe='H' THEN 1 ELSE 0 END) AS nb_h,
               SUM(CASE WHEN j.sexe='F' THEN 1 ELSE 0 END) AS nb_f,
               CAST(AVG(j.classement) AS INT) AS classement_moyen,
               MIN(j.classement) AS meilleur_classement
        FROM joueurs j LEFT JOIN clubs cl ON cl.id = j.club_id
        WHERE j.club_id IS NOT NULL AND j.dernier_mois_vu IS NOT NULL
        GROUP BY j.club_id""")
    print(f"  stats_geo_club: {c.execute('SELECT COUNT(*) FROM stats_geo_club').fetchone()[0]:,} lignes")

    if SHOW:
        print("\n— Top régions (nb joueurs) —")
        for r in c.execute("SELECT ligue, nb_total, nb_h, nb_f FROM stats_geo_region ORDER BY nb_total DESC LIMIT 8"):
            print(f"   {r[0]:30} {r[1]:>7,}  (H {r[2]:,} / F {r[3]:,})")
        print("\n— Top départements —")
        for r in c.execute("SELECT dept_num, comite, nb_total FROM stats_geo_departement ORDER BY nb_total DESC LIMIT 8"):
            print(f"   {r[0]:>3} {r[1]:25} {r[2]:>7,}")
        print("\n— Top clubs (nb membres) —")
        for r in c.execute("SELECT club_nom, ville, nb_membres FROM stats_geo_club ORDER BY nb_membres DESC LIMIT 8"):
            print(f"   {str(r[0])[:32]:32} {str(r[1])[:18]:18} {r[2]:>5}")


if __name__ == '__main__':
    main()
