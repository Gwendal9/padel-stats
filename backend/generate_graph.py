"""
Génère une visualisation graphe interactive (style Obsidian)
des joueurs padel et leurs partenariats.

Usage : python generate_graph.py
"""
import sqlite3, json, os, colorsys as _cs
from collections import Counter

DB_FILE  = os.path.join(os.path.dirname(__file__), 'tenup.db')
OUT_FILE = os.path.join(os.path.dirname(__file__), 'graph_padel.html')

CLUBS_FILE = os.path.join(os.path.dirname(__file__), 'clubs.json')

conn = sqlite3.connect(f"file:{DB_FILE}?mode=ro", uri=True)

# ── Joueurs (nœuds) ──────────────────────────────────────────────────
rows_j = conn.execute("""
    SELECT j.id_fft, j.nom, j.prenom,
           COALESCE(NULLIF(j.club_nom,''), j.ville) AS club,
           j.echelon, j.classement, j.sexe,
           COUNT(DISTINCT p.id_tournoi) as nb_tournois
    FROM joueurs j
    LEFT JOIN participations p ON p.id_joueur = j.id_fft
    GROUP BY j.id_fft
""").fetchall()

nodes = []
id_to_idx = {}
for i, (id_fft, nom, prenom, club, echelon, classement, sexe, nb_t) in enumerate(rows_j):
    id_to_idx[id_fft] = i
    nodes.append({
        "id":         id_fft,
        "label":      f"{prenom or ''} {nom or ''}".strip() or id_fft,
        "club":       club or "",
        "echelon":    echelon or "",
        "classement": classement or 0,
        "sexe":       sexe or "",
        "nb_tournois": nb_t or 0,
    })

# Top 15 villes/clubs pour les modes couleur "ville" et "club"
ville_counts = Counter(n["club"] for n in nodes if n["club"])
top_clubs = [v for v, _ in ville_counts.most_common(15)]

# ── Liens (dédupliqués, poids = nb tournois joués ensemble) ──────────
rows_l = conn.execute("""
    SELECT
        MIN(id_joueur, partenaire_id) as src,
        MAX(id_joueur, partenaire_id) as tgt,
        COUNT(DISTINCT id_tournoi)    as weight
    FROM participations
    WHERE partenaire_id IS NOT NULL AND partenaire_id != ''
      AND id_joueur    IN (SELECT id_fft FROM joueurs)
      AND partenaire_id IN (SELECT id_fft FROM joueurs)
    GROUP BY src, tgt
""").fetchall()

links = []
for src, tgt, w in rows_l:
    if src in id_to_idx and tgt in id_to_idx:
        links.append({"source": id_to_idx[src], "target": id_to_idx[tgt], "weight": w})

conn.close()

# Charger le mapping ville → département
clubs_map = {}
if os.path.exists(CLUBS_FILE):
    with open(CLUBS_FILE, encoding='utf-8') as f:
        raw = json.load(f)
    raw.pop('_note', None)
    clubs_map = raw
    print(f"🗺️  {len(clubs_map)} villes mappées dans clubs.json")

# Enrichir les nœuds avec le département
for n in nodes:
    info = clubs_map.get(n['club'], {})
    n['dept'] = info.get('dept', '')

# Départements uniques pour la palette
all_depts = sorted(set(n['dept'] for n in nodes if n['dept']))
print(f"📊 {len(nodes)} nœuds, {len(links)} liens, {len(all_depts)} départements")

# ── Régions françaises → positions hiérarchiques ─────────────────────────────
# Layout : 14 régions en cercle, joueurs de chaque région en spirale Fibonacci.
# La répulsion cross-région dans le JS maintient la séparation lors des interactions.
import math as _math
from collections import defaultdict as _dd

_GOLDEN = 2.39996   # angle d'or ≈ 137.5°
_W, _H  = 3200, 1800

# (code, label, couleur hex, départements)
_REGIONS = [
    ('IDF', '\u00cele-de-France',         '#58a6ff', ['75','77','78','91','92','93','94','95']),
    ('ARA', 'Auvergne-Rh\u00f4ne-Alpes', '#3fb950', ['01','03','07','15','26','38','42','43','63','69','73','74']),
    ('BFC', 'Bourgogne-FC',               '#bc8cff', ['21','25','39','58','70','71','89','90']),
    ('BRE', 'Bretagne',                   '#39d353', ['22','29','35','56']),
    ('CVL', 'Centre-Val-de-Loire',        '#d29922', ['18','28','36','37','41','45']),
    ('COR', 'Corse',                      '#f0b429', ['2A','2B']),
    ('GRE', 'Grand Est',                  '#79c0ff', ['08','10','51','52','54','55','57','67','68','88']),
    ('HDF', 'Hauts-de-France',            '#ff7b72', ['02','59','60','62','80']),
    ('NOR', 'Normandie',                  '#f78166', ['14','27','50','61','76']),
    ('NAQ', 'Nouvelle-Aquitaine',         '#4a9eff', ['16','17','19','23','24','33','40','47','64','79','86','87']),
    ('OCC', 'Occitanie',                  '#ff9f43', ['09','11','12','30','31','32','34','46','48','65','66','81','82']),
    ('PDL', 'Pays de la Loire',           '#a29bfe', ['44','49','53','72','85']),
    ('PAC', 'PACA',                       '#fd79a8', ['04','05','06','13','83','84']),
    ('DOM', 'DOM-TOM',                    '#b2bec3', ['971','972','973','974','975','976','977','978','988','98','99']),
]

# Mapping dept → region
_dept_to_region = {}
for _rc, _rl, _rcol, _rdepts in _REGIONS:
    for _d in _rdepts:
        _dept_to_region[_d] = _rc

# Enrichir les nœuds avec la région
for _n in nodes:
    _n['region'] = _dept_to_region.get(_n.get('dept',''), '')

# Centres des régions sur le canvas
_R_region = min(_W, _H) * 0.40
_region_center = {}
_regions_present = [r for r in _REGIONS if any(n['region'] == r[0] for n in nodes)]
for _i, (_rc, *_) in enumerate(_regions_present):
    _ang = (_i / len(_regions_present)) * 2 * _math.pi - _math.pi / 2
    _region_center[_rc] = (
        _W/2 + _R_region * _math.cos(_ang),
        _H/2 + _R_region * _math.sin(_ang),
    )

# Positionner chaque nœud en spirale Fibonacci autour du centre de sa région
_by_region = _dd(list)
_unmapped   = []
for _n in nodes:
    (_by_region[_n['region']] if _n['region'] else _unmapped).append(_n)

for _rc, _rnodes in _by_region.items():
    _rnodes.sort(key=lambda n: n['nb_tournois'], reverse=True)
    _tx, _ty = _region_center.get(_rc, (_W/2, _H/2))
    for _idx, _n in enumerate(_rnodes):
        _r = 18 * _math.sqrt(_idx + 1)   # ×18 pour éviter les chevauchements
        _n['px'] = round(_tx + _r * _math.cos(_idx * _GOLDEN), 1)
        _n['py'] = round(_ty + _r * _math.sin(_idx * _GOLDEN), 1)

for _idx, _n in enumerate(_unmapped):
    _r = 40 + 14 * _math.sqrt(_idx + 1)
    _n['px'] = round(_W/2 + _r * _math.cos(_idx * _GOLDEN), 1)
    _n['py'] = round(_H/2 + _r * _math.sin(_idx * _GOLDEN), 1)

print(f"✅  Positions pré-calculées ({len(nodes)} nœuds, {len(_regions_present)} régions)")

# ── Couleurs par département (teintes dérivées de la couleur de la région) ──────
# Chaque dept obtient une nuance unique dans la famille de couleur de sa région.
# On fait varier la luminosité de 0.30 (foncé) à 0.68 (clair) et la teinte ±5°.
def _hex_to_hls(h):
    r, g, b = int(h[1:3],16)/255, int(h[3:5],16)/255, int(h[5:7],16)/255
    return _cs.rgb_to_hls(r, g, b)   # (H, L, S)

def _hls_to_hex(h, l, s):
    r, g, b = _cs.hls_to_rgb(h, l, s)
    return '#{:02x}{:02x}{:02x}'.format(round(r*255), round(g*255), round(b*255))

_dept_colors_fine = {}
for _rc, _rl, _rcol, _rdepts in _REGIONS:
    if not _rdepts: continue
    _H, _L, _S = _hex_to_hls(_rcol)
    _n_d = max(len(_rdepts), 1)
    for _i_d, _d in enumerate(_rdepts):
        _t = _i_d / (_n_d - 1) if _n_d > 1 else 0.5
        _new_l = 0.30 + _t * 0.38        # luminosité 0.30 → 0.68
        _new_h = (_H + (_i_d - _n_d/2) * 0.018) % 1.0   # teinte ±quelques degrés
        _dept_colors_fine[_d] = _hls_to_hex(_new_h, _new_l, min(_S * 1.1, 1.0))

print(f"🎨  {len(_dept_colors_fine)} couleurs dept générées")

# Données régions pour le JS (seulement celles présentes dans la base)
regions_data = [
    {"code": _rc, "label": _rl, "color": _rcol, "depts": _rdepts}
    for _rc, _rl, _rcol, _rdepts in _REGIONS
    if _rc in _region_center
]

# ── Statistiques par département (pour la carte choroplèthe) ──────────────────
_dept_stats = {}
for _n in nodes:
    _d = _n.get('dept', '')
    if not _d: continue
    if _d not in _dept_stats:
        _dept_stats[_d] = {'nb_joueurs': 0, 'sum_e': 0, 'cnt_e': 0, 'nb_t': 0, 'top_p': []}
    _ds = _dept_stats[_d]
    _ds['nb_joueurs'] += 1
    _ds['nb_t'] += _n.get('nb_tournois', 0)
    try:
        _e = int(_n.get('echelon') or 0)
        if _e > 0: _ds['sum_e'] += _e; _ds['cnt_e'] += 1
    except: pass
    _ds['top_p'].append(_n)

_dept_stats_out = {}
for _d, _ds in _dept_stats.items():
    _top = sorted(_ds['top_p'], key=lambda x: x.get('nb_tournois', 0), reverse=True)[:15]
    _dept_stats_out[_d] = {
        'nb_joueurs': _ds['nb_joueurs'],
        'avg_echelon': round(_ds['sum_e'] / _ds['cnt_e']) if _ds['cnt_e'] else 0,
        'nb_tournois': _ds['nb_t'],
        'top_players': [
            {'idx': id_to_idx.get(_n['id'], -1), 'label': _n['label'],
             'nb_t': _n.get('nb_tournois', 0), 'echelon': _n.get('echelon', '')}
            for _n in _top
        ],
    }
print(f"📊  Stats pour {len(_dept_stats_out)} departements")

# ensure_ascii=True pour éviter tout caractère spécial qui casserait le JS
graph_data = json.dumps({
    "nodes": nodes, "links": links,
    "topVilles": top_clubs,
    "allDepts": all_depts,
    "regions": regions_data,
    "deptColorsFine": _dept_colors_fine,
    "deptStats": _dept_stats_out,
}, ensure_ascii=True)


# ── HTML (nouveau : Carte France + Réseau Ego) ───────────────────────────────
html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<title>Padel — Réseau</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#0d1117;color:#e6edf3;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;overflow:hidden;height:100vh}}

/* ── Top bar ── */
#topbar{{
  position:fixed;top:0;left:0;right:0;z-index:50;
  height:50px;background:rgba(13,17,23,.97);border-bottom:1px solid #30363d;
  display:flex;align-items:center;gap:7px;padding:0 14px;
}}
#search-wrap{{position:relative}}
#search-input{{
  background:#161b22;border:1px solid #30363d;color:#e6edf3;
  padding:5px 10px;font-size:12px;width:210px;outline:none;border-radius:6px;
}}
#search-input::placeholder{{color:#484f58}}
#search-dropdown{{
  position:fixed;background:#161b22;border:1px solid #30363d;border-top:none;
  z-index:200;max-height:280px;overflow-y:auto;border-radius:0 0 6px 6px;display:none;
}}
.search-item{{padding:8px 12px;cursor:pointer;font-size:12px;color:#e6edf3;border-bottom:1px solid #21262d}}
.search-item:hover{{background:#1c2128}}
.sep{{width:1px;height:20px;background:#30363d;margin:0 2px}}
.view-tab{{
  background:none;border:1px solid transparent;color:#8b949e;
  padding:4px 12px;border-radius:6px;font-size:12px;cursor:pointer;transition:all .15s;
}}
.view-tab:hover{{color:#e6edf3;border-color:#30363d}}
.view-tab.active{{color:#58a6ff;border-color:#58a6ff;background:rgba(88,166,255,.1)}}
.color-btn{{
  background:none;border:1px solid transparent;color:#8b949e;
  padding:4px 8px;border-radius:6px;font-size:11px;cursor:pointer;transition:all .15s;white-space:nowrap;
}}
.color-btn:hover{{color:#e6edf3;border-color:#30363d}}
.color-btn.active{{color:#58a6ff;border-color:#58a6ff;background:rgba(88,166,255,.1)}}
.action-btn{{
  background:none;border:1px solid #30363d;color:#8b949e;
  padding:4px 9px;border-radius:6px;font-size:11px;cursor:pointer;transition:all .15s;
}}
.action-btn:hover{{color:#e6edf3;border-color:#8b949e}}

/* ── Map view ── */
#map-view{{position:fixed;top:50px;left:0;right:0;bottom:0;display:flex}}
#map-view.hidden{{display:none}}
#map-container{{flex:1;position:relative;overflow:hidden;background:#0d1117}}
#france-map{{width:100%;height:100%}}
.dept-path{{stroke:#21262d;stroke-width:0.5;cursor:pointer;transition:stroke .1s,stroke-width .1s,opacity .1s}}
.dept-path:hover{{stroke:#58a6ff;stroke-width:1.5;opacity:.8}}
.dept-path.selected{{stroke:#58a6ff;stroke-width:2.5}}

/* Map side panel */
#map-panel{{
  width:310px;min-width:310px;border-left:1px solid #30363d;
  background:#0d1117;display:flex;flex-direction:column;overflow:hidden;
}}
#mp-header{{padding:16px;border-bottom:1px solid #21262d;flex-shrink:0}}
#mp-title{{font-size:14px;font-weight:600;color:#e6edf3;display:flex;align-items:center;gap:7px}}
#mp-subtitle{{font-size:11px;color:#8b949e;margin-top:3px}}
.mp-stats{{display:flex;flex-shrink:0;border-bottom:1px solid #21262d}}
.mp-stat-box{{flex:1;padding:10px;text-align:center;border-right:1px solid #21262d}}
.mp-stat-box:last-child{{border-right:none}}
.mp-stat-val{{font-size:17px;font-weight:700;color:#58a6ff}}
.mp-stat-lbl{{font-size:10px;color:#484f58;margin-top:1px}}
.mp-section{{padding:7px 14px;font-size:10px;color:#484f58;text-transform:uppercase;letter-spacing:.8px;border-bottom:1px solid #21262d;flex-shrink:0}}
#mp-players{{flex:1;overflow-y:auto}}
.mp-player{{
  padding:9px 14px;cursor:pointer;display:flex;justify-content:space-between;
  align-items:center;border-bottom:1px solid #1c2128;transition:background .1s;
}}
.mp-player:hover{{background:#161b22}}
.mp-player-name{{font-size:12px;color:#e6edf3}}
.mp-player-meta{{font-size:11px;color:#484f58;margin-top:1px}}
.mp-btn{{
  background:rgba(88,166,255,.1);border:1px solid rgba(88,166,255,.3);color:#58a6ff;
  padding:3px 8px;border-radius:5px;font-size:10px;cursor:pointer;white-space:nowrap;flex-shrink:0;
}}
.mp-btn:hover{{background:rgba(88,166,255,.2)}}
#mp-empty{{padding:40px 20px;text-align:center;color:#484f58;font-size:13px;line-height:1.9}}

/* Map metric buttons */
#map-metric{{position:absolute;top:10px;left:10px;z-index:10;display:flex;gap:4px}}
.metric-btn{{
  background:rgba(13,17,23,.88);border:1px solid #30363d;color:#8b949e;
  padding:4px 9px;border-radius:5px;font-size:11px;cursor:pointer;transition:all .15s;backdrop-filter:blur(4px);
}}
.metric-btn:hover{{color:#e6edf3;border-color:#8b949e}}
.metric-btn.active{{color:#58a6ff;border-color:#58a6ff}}

/* Map legend */
#map-legend{{
  position:absolute;bottom:14px;left:14px;z-index:10;
  background:rgba(13,17,23,.88);border:1px solid #30363d;border-radius:6px;
  padding:9px 13px;backdrop-filter:blur(4px);
}}
.ml-title{{font-size:10px;color:#484f58;text-transform:uppercase;letter-spacing:.7px;margin-bottom:5px}}
.ml-bar{{width:150px;height:7px;border-radius:3px}}
.ml-labels{{display:flex;justify-content:space-between;font-size:10px;color:#8b949e;margin-top:3px}}

/* Map tooltip */
#map-tooltip{{
  position:absolute;pointer-events:none;display:none;
  background:rgba(13,17,23,.95);border:1px solid #30363d;border-radius:6px;
  padding:8px 12px;font-size:12px;color:#e6edf3;z-index:60;line-height:1.7;white-space:nowrap;
}}

/* ── Graph view ── */
#graph-view{{position:fixed;top:50px;left:0;right:0;bottom:0;display:none}}
#graph-view.active{{display:block}}
#graph-canvas{{width:100%;height:100%;cursor:grab}}
#graph-canvas:active{{cursor:grabbing}}

/* Graph controls bar */
#graph-controls{{
  position:absolute;top:10px;left:50%;transform:translateX(-50%);
  z-index:20;display:flex;gap:8px;align-items:center;
  background:rgba(22,27,34,.92);border:1px solid #30363d;
  border-radius:10px;padding:7px 12px;backdrop-filter:blur(8px);
}}
#graph-title{{font-size:12px;font-weight:600;color:#e6edf3;max-width:240px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.depth-lbl{{font-size:11px;color:#8b949e;white-space:nowrap}}
#depth-slider{{accent-color:#58a6ff;cursor:pointer;width:72px}}
#depth-val{{color:#58a6ff;font-weight:700;font-size:13px;min-width:10px}}
#graph-stats-small{{font-size:11px;color:#484f58}}

/* Graph legend */
#graph-legend{{
  position:absolute;bottom:56px;left:50%;transform:translateX(-50%);z-index:10;
  background:rgba(22,27,34,.85);border:1px solid #30363d;border-radius:6px;
  padding:6px 14px;font-size:11px;color:#8b949e;
  display:flex;gap:12px;align-items:center;flex-wrap:wrap;justify-content:center;max-width:90vw;
}}
.legend-item{{display:flex;align-items:center;gap:4px;white-space:nowrap}}
.legend-dot{{width:9px;height:9px;border-radius:50%;flex-shrink:0}}
.legend-bar{{width:80px;height:7px;border-radius:3px;flex-shrink:0}}

/* Graph statsbar */
#statsbar{{
  position:absolute;bottom:14px;left:14px;z-index:10;
  background:rgba(22,27,34,.85);border:1px solid #30363d;
  border-radius:6px;padding:6px 12px;font-size:11px;color:#8b949e;
  display:flex;gap:14px;align-items:center;
}}
#min-weight-wrap{{display:flex;align-items:center;gap:7px}}
#min-weight{{accent-color:#58a6ff;cursor:pointer}}

/* Hint */
#hint{{
  position:absolute;bottom:14px;right:14px;z-index:10;
  background:rgba(22,27,34,.85);border:1px solid #30363d;
  border-radius:6px;padding:6px 12px;font-size:10px;color:#484f58;line-height:1.8;
}}

/* Player panel */
#player-panel{{
  position:absolute;top:0;right:0;width:290px;height:100%;
  background:rgba(13,17,23,.97);border-left:1px solid #30363d;
  padding:20px 16px;overflow-y:auto;
  transform:translateX(100%);transition:transform .25s cubic-bezier(.4,0,.2,1);z-index:30;
}}
#player-panel.open{{transform:translateX(0)}}
#pp-close{{position:absolute;top:12px;right:12px;background:none;border:none;color:#484f58;font-size:18px;cursor:pointer}}
#pp-close:hover{{color:#e6edf3}}
#pp-name{{font-size:15px;font-weight:600;color:#e6edf3;margin-bottom:2px;padding-right:22px}}
#pp-sub{{font-size:11px;color:#8b949e;margin-bottom:14px}}
.ppstat{{display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid #21262d;font-size:12px}}
.ppstat-val{{color:#58a6ff;font-weight:600}}
.ppsection{{margin:12px 0 6px;font-size:10px;color:#484f58;text-transform:uppercase;letter-spacing:.8px}}
.pppartner{{
  padding:6px 0;font-size:12px;border-bottom:1px solid #21262d;
  cursor:pointer;display:flex;justify-content:space-between;align-items:center;
}}
.pppartner:hover{{color:#58a6ff}}
.pppartner-cnt{{color:#484f58;font-size:11px}}
.pp-goto-btn{{
  background:rgba(88,166,255,.1);border:1px solid rgba(88,166,255,.3);color:#58a6ff;
  padding:2px 6px;border-radius:4px;font-size:10px;cursor:pointer;flex-shrink:0;margin-left:6px;
}}
.pp-goto-btn:hover{{background:rgba(88,166,255,.2)}}
</style>
</head>
<body>

<!-- ── Top bar ── -->
<div id="topbar">
  <span style="color:#58a6ff;font-weight:700;font-size:13px;white-space:nowrap">🎾 Padel</span>
  <div class="sep"></div>
  <div id="search-wrap">
    <input id="search-input" type="text" placeholder="🔍 Rechercher un joueur...">
    <div id="search-dropdown"></div>
  </div>
  <div class="sep"></div>
  <button class="view-tab active" id="tab-map">🗺 Carte</button>
  <button class="view-tab" id="tab-graph">🕸 Réseau</button>
  <div class="sep"></div>
  <button class="color-btn active" data-mode="niveau">Niveau</button>
  <button class="color-btn" data-mode="sexe">Sexe</button>
  <button class="color-btn" data-mode="region">Région</button>
  <button class="color-btn" data-mode="dept">Dép.</button>
  <button class="color-btn" data-mode="club">Club</button>
  <button class="color-btn" data-mode="activite">Activité</button>
  <button class="color-btn" data-mode="connexions">Connexions</button>
  <div class="sep"></div>
  <button class="action-btn" id="btn-shake" title="Secouer">🔀</button>
  <button class="action-btn" id="btn-reset" title="Reset">↺</button>
</div>

<!-- ── Map view ── -->
<div id="map-view">
  <div id="map-container">
    <div id="map-metric">
      <button class="metric-btn active" data-metric="nb_joueurs">Joueurs</button>
      <button class="metric-btn" data-metric="nb_tournois">Tournois</button>
      <button class="metric-btn" data-metric="avg_echelon">\u00c9chelon moy.</button>
    </div>
    <svg id="france-map"></svg>
    <div id="map-legend">
      <div class="ml-title" id="ml-title">—</div>
      <div class="ml-bar" id="ml-bar"></div>
      <div class="ml-labels"><span id="ml-min"></span><span id="ml-max"></span></div>
    </div>
    <div id="map-tooltip"></div>
  </div>
  <div id="map-panel">
    <div id="mp-empty">Cliquez sur un département<br>pour voir ses joueurs<br><br>
      <span style="font-size:11px;color:#30363d">ou recherchez un joueur ↑</span>
    </div>
  </div>
</div>

<!-- ── Graph view ── -->
<div id="graph-view">
  <svg id="graph-canvas"></svg>
  <div id="graph-controls">
    <div id="graph-title">—</div>
    <div class="sep" style="width:1px;height:18px;background:#30363d;margin:0 2px"></div>
    <span class="depth-lbl">Profondeur</span>
    <input type="range" id="depth-slider" min="1" max="4" value="2">
    <span id="depth-val">2</span>
    <div class="sep" style="width:1px;height:18px;background:#30363d;margin:0 2px"></div>
    <span id="graph-stats-small">— nœuds</span>
  </div>
  <div id="statsbar">
    <span><span id="s-nodes">0</span> joueurs</span>
    <span><span id="s-links">0</span> liens</span>
    <div style="width:1px;height:16px;background:#30363d;margin:0 4px"></div>
    <div id="min-weight-wrap">
      Liens ≥ <input id="min-weight" type="range" min="1" max="10" value="2" style="width:65px">
      <span id="mw-val">2</span>x
    </div>
  </div>
  <div id="graph-legend"></div>
  <div id="hint">Clic : isoler · Glisser : ancrer · Clic droit : libérer · Molette : zoom</div>
  <div id="player-panel">
    <button id="pp-close">✕</button>
    <div id="pp-name"></div>
    <div id="pp-sub"></div>
    <div class="ppstat"><span>Tournois</span><span class="ppstat-val" id="pp-tournois"></span></div>
    <div class="ppstat"><span>Partenaires</span><span class="ppstat-val" id="pp-npartners"></span></div>
    <div class="ppstat"><span>Échelon FFT</span><span class="ppstat-val" id="pp-echelon"></span></div>
    <div class="ppstat"><span>Club</span><span class="ppstat-val" id="pp-ville"></span></div>
    <div class="ppstat"><span>Région</span><span class="ppstat-val" id="pp-region"></span></div>
    <div class="ppsection">Partenaires (clic = voir réseau)</div>
    <div id="pp-partners"></div>
  </div>
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/d3/7.9.0/d3.min.js"></script>
<script>
const RAW       = {graph_data};
const ALL_REGIONS = RAW.regions || [];
const TOP_VILLES  = RAW.topVilles;
const ALL_DEPTS   = RAW.allDepts;
const DEPT_STATS  = RAW.deptStats || {{}};

// ── Palettes ──────────────────────────────────────────────────────────────────
const VILLE_PALETTE = [
  '#58a6ff','#3fb950','#f78166','#d29922','#bc8cff',
  '#39d353','#ff7b72','#79c0ff','#fd79a8','#ff9f43',
  '#a29bfe','#00cec9','#e17055','#fdcb6e','#74b9ff',
];
const villeColor = {{}};
TOP_VILLES.forEach((v,i) => villeColor[v] = VILLE_PALETTE[i % VILLE_PALETTE.length]);

const REGION_PALETTE = {{}};
ALL_REGIONS.forEach(r => {{ REGION_PALETTE[r.code] = r.color; }});
const DEPT_PALETTE = {{}};
ALL_REGIONS.forEach(r => r.depts.forEach(d => {{ DEPT_PALETTE[d] = r.color; }}));
const DEPT_COLOR_FINE = RAW.deptColorsFine || {{}};

function regionColor(d) {{ return REGION_PALETTE[d.region] || '#484f58'; }}
function deptColor(d)   {{ return DEPT_COLOR_FINE[d.dept] || DEPT_PALETTE[d.dept] || '#484f58'; }}
function clubColor(d)   {{ return villeColor[d.club] || '#484f58'; }}

// ── Paliers d'échelon FFT padel ─────────────────────────────────────────────
// L'échelon est un NIVEAU DE POINTS, pas un rang : 60 = débutant, 300+ = élite.
// Valeurs discrètes : 60, 65, 70, 80, 90, 100, 110 … 250, 300, 350…
// PLUS l'échelon est ÉLEVÉ, meilleur est le joueur.
const NIVEAU_TIERS = [
  [60,  60,  '#3fb950', '\u00c9ch. 60 (d\u00e9butant)'],
  [65,  75,  '#52c97a', '\u00c9ch. 65\u201375'],
  [80,  90,  '#8ed357', '\u00c9ch. 80\u201390'],
  [100, 110, '#d3c557', '\u00c9ch. 100\u2013110'],
  [120, 130, '#d29922', '\u00c9ch. 120\u2013130'],
  [140, 150, '#ff9f43', '\u00c9ch. 140\u2013150'],
  [160, 170, '#ff7b72', '\u00c9ch. 160\u2013170'],
  [180, 190, '#e06c75', '\u00c9ch. 180\u2013190'],
  [200, 210, '#db61a2', '\u00c9ch. 200\u2013210'],
  [220, 249, '#bc8cff', '\u00c9ch. 220\u2013240'],
  [250, 299, '#f0b429', '\u00c9ch. 250\u2013290'],
  [300, null,'#ffd700', '\u00c9ch. 300+ (\u00e9lite)'],
];
function niveauColor(echelon) {{
  const e = parseInt(echelon) || 0;
  if (!e) return '#484f58';
  for (const [lo, hi, col] of NIVEAU_TIERS) {{
    if (e >= lo && (hi === null || e <= hi)) return col;
  }}
  return '#ffd700';
}}

let colorMode = 'niveau';
const allDegree = {{}};
RAW.links.forEach(l => {{
  allDegree[l.source] = (allDegree[l.source]||0)+1;
  allDegree[l.target] = (allDegree[l.target]||0)+1;
}});
const maxTournois = Math.max(1, ...RAW.nodes.map(n=>n.nb_tournois||0));
const maxDegree   = Math.max(1, ...Object.values(allDegree));
const scaleAct    = d3.scaleSequential([0,maxTournois], d3.interpolateYlOrRd);
const scaleConn   = d3.scaleSequential([0,maxDegree],   d3.interpolateCool);

function getColor(d, origIdx) {{
  switch(colorMode) {{
    case 'niveau':     return niveauColor(d.echelon);
    case 'sexe':       return d.sexe==='H'?'#58a6ff': d.sexe==='F'?'#db61a2':'#484f58';
    case 'region':     return regionColor(d);
    case 'dept':       return deptColor(d);
    case 'club':       return clubColor(d);
    case 'activite':   return d.nb_tournois>0 ? scaleAct(d.nb_tournois) : '#484f58';
    case 'connexions': return (allDegree[origIdx]||0)>0 ? scaleConn(allDegree[origIdx]) : '#484f58';
  }}
  return '#484f58';
}}
function nodeRadius(d) {{ return Math.max(4, Math.min(16, 4 + Math.sqrt(d.nb_tournois||1) * 1.5)); }}
function linkWidth(w)  {{ return Math.min(5, 0.25 + Math.pow(w||1, 1.1) * 0.2); }}
function linkOpacity(w){{ return Math.min(0.75, 0.10 + (w||1) * 0.06); }}
function linkColor(w)  {{
  if(w>=8) return '#f0b429'; if(w>=5) return '#58a6ff'; if(w>=3) return '#4a5568'; return '#2d3748';
}}

// ── BFS ego network ───────────────────────────────────────────────────────────
function computeEgoNetwork(centerIdx, depth, minWeight) {{
  const visited = new Set([centerIdx]);
  let frontier  = new Set([centerIdx]);
  const depthOf = new Map([[centerIdx, 0]]);

  for (let d = 0; d < depth; d++) {{
    const next = new Set();
    RAW.links.forEach(l => {{
      if (l.weight < minWeight) return;
      const s = l.source, t = l.target;
      if (frontier.has(s) && !visited.has(t)) {{ next.add(t); visited.add(t); depthOf.set(t, d+1); }}
      if (frontier.has(t) && !visited.has(s)) {{ next.add(s); visited.add(s); depthOf.set(s, d+1); }}
    }});
    frontier = next;
    if (!frontier.size) break;
  }}

  const oldArr   = [...visited];
  const indexMap = new Map(oldArr.map((old, i) => [old, i]));
  const subNodes = oldArr.map(i => ({{...RAW.nodes[i], _origIdx: i, _depth: depthOf.get(i)||0}}));
  const subLinks = RAW.links
    .filter(l => visited.has(l.source) && visited.has(l.target) && l.weight >= minWeight)
    .map(l => ({{source: indexMap.get(l.source), target: indexMap.get(l.target), weight: l.weight}}));

  return {{ nodes: subNodes, links: subLinks, centerNewIdx: indexMap.get(centerIdx) }};
}}

// ── MAP VIEW ──────────────────────────────────────────────────────────────────
let mapGeoJSON   = null;
let mapMetric    = 'nb_joueurs';
let mapPath, mapSvg, mapG;

const MAP_METRICS = {{
  nb_joueurs:  {{ label:'Joueurs par dép.',   grad:'#0d1f2e,#58a6ff' }},
  nb_tournois: {{ label:'Tournois par dép.',  grad:'#0d1f1a,#3fb950' }},
  avg_echelon: {{ label:'\u00c9chelon moyen (haut = meilleur)', grad:'#0d1f1a,#ffd700' }},
}};

function initMap() {{
  const cont = document.getElementById('map-container');
  const W = cont.clientWidth, H = cont.clientHeight;
  mapSvg = d3.select('#france-map').attr('viewBox', [0,0,W,H]);
  const proj = d3.geoConicConformal()
    .center([2.454071, 46.279229])
    .scale(Math.min(W,H) * 2.85)
    .translate([W * 0.43, H * 0.52]);
  mapPath = d3.geoPath().projection(proj);
  mapG = mapSvg.append('g');

  // Zoom / pan sur la carte
  const mapZoom = d3.zoom().scaleExtent([0.5, 12])
    .on('zoom', e => {{ mapG.attr('transform', e.transform); }});
  mapSvg.call(mapZoom);
  // Double-clic sur fond = reset zoom
  mapSvg.on('dblclick.zoom', null);
  mapSvg.on('dblclick', () => {{
    mapSvg.transition().duration(500).call(mapZoom.transform, d3.zoomIdentity);
  }});

  const tooltip = document.getElementById('map-tooltip');

  d3.json('https://raw.githubusercontent.com/gregoiredavid/france-geojson/master/departements-version-simplifiee.geojson')
    .then(geo => {{
      mapGeoJSON = geo;
      renderMap();

      mapG.selectAll('.dept-path')
        .on('mousemove', (e, feat) => {{
          const code = feat.properties.code;
          const nom  = feat.properties.nom;
          const st   = DEPT_STATS[code];
          let html2  = `<strong>${{nom}}</strong> (${{'0'.repeat(2-code.length)}}${{code}})<br>`;
          if (st) {{
            html2 += `${{st.nb_joueurs}} joueurs · ${{st.nb_tournois}} tournois`;
            if (st.avg_echelon) html2 += `<br>\u00c9chelon moy. : ${{st.avg_echelon}}`;
          }} else {{
            html2 += `<span style="color:#484f58">Aucun joueur dans la base</span>`;
          }}
          tooltip.innerHTML = html2;
          tooltip.style.display = 'block';
          tooltip.style.left = (e.offsetX+14)+'px';
          tooltip.style.top  = (e.offsetY+14)+'px';
        }})
        .on('mouseleave', () => {{ tooltip.style.display='none'; }})
        .on('click', (e, feat) => {{
          e.stopPropagation();
          mapG.selectAll('.dept-path').classed('selected', false);
          d3.select(e.currentTarget).classed('selected', true);
          showDeptPanel(feat.properties.code, feat.properties.nom);
        }});

      mapSvg.on('click', () => {{
        mapG.selectAll('.dept-path').classed('selected', false);
        resetMapPanel();
      }});
    }})
    .catch(() => {{
      const W2 = cont.clientWidth, H2 = cont.clientHeight;
      mapG.append('text').attr('x',W2/2).attr('y',H2/2)
        .attr('text-anchor','middle').attr('fill','#484f58').attr('font-size','14')
        .text('Carte non disponible — ouvrez avec un serveur local ou connexion internet');
    }});
}}

function mapColorScale(metric) {{
  const vals = Object.values(DEPT_STATS).map(s => s[metric]||0).filter(v=>v>0);
  if (!vals.length) return () => '#161b22';
  const maxV = d3.max(vals);
  const [c0, c1] = MAP_METRICS[metric].grad.split(',');
  return d3.scaleSequential([0, maxV], d3.interpolateRgb(c0, c1));
}}

function renderMap() {{
  if (!mapGeoJSON) return;
  const scale = mapColorScale(mapMetric);
  const vals  = Object.values(DEPT_STATS).map(s => s[mapMetric]||0).filter(v=>v>0);
  const maxV  = vals.length ? d3.max(vals) : 0;
  const minV  = vals.length ? d3.min(vals) : 0;
  const info  = MAP_METRICS[mapMetric];
  document.getElementById('ml-title').textContent = info.label;
  const [c0,c1] = info.grad.split(',');
  document.getElementById('ml-bar').style.background = `linear-gradient(to right,${{c0}},${{c1}})`;
  document.getElementById('ml-min').textContent = minV;
  document.getElementById('ml-max').textContent = maxV;

  mapG.selectAll('.dept-path').remove();
  mapG.selectAll('.dept-path')
    .data(mapGeoJSON.features)
    .join('path')
    .attr('class','dept-path')
    .attr('d', mapPath)
    .attr('fill', feat => {{
      const st = DEPT_STATS[feat.properties.code];
      if (!st || !st[mapMetric]) return '#161b22';
      return scale(st[mapMetric]);
    }});
}}

function resetMapPanel() {{
  document.getElementById('map-panel').innerHTML =
    '<div id="mp-empty">Cliquez sur un département<br>pour voir ses joueurs<br><br>' +
    '<span style="font-size:11px;color:#30363d">ou recherchez un joueur \u2191</span></div>';
}}

function showDeptPanel(code, nom) {{
  const st = DEPT_STATS[code];
  const panel = document.getElementById('map-panel');
  const region = ALL_REGIONS.find(r => r.depts.includes(code)) || {{}};
  const rcolor = region.color || '#58a6ff';
  const rlabel = region.label || '—';

  if (!st) {{
    panel.innerHTML = `<div id="mp-header">
      <div id="mp-title"><span style="width:9px;height:9px;border-radius:50%;background:#484f58;display:inline-block;flex-shrink:0"></span>${{nom}} (${{code}})</div>
      <div id="mp-subtitle">Aucun joueur dans la base</div>
    </div>`;
    return;
  }}

  panel.innerHTML = `
    <div id="mp-header">
      <div id="mp-title">
        <span style="width:9px;height:9px;border-radius:50%;background:${{rcolor}};display:inline-block;flex-shrink:0"></span>
        ${{nom}} (${{code}})
      </div>
      <div id="mp-subtitle">${{rlabel}}</div>
    </div>
    <div class="mp-stats">
      <div class="mp-stat-box"><div class="mp-stat-val">${{st.nb_joueurs}}</div><div class="mp-stat-lbl">Joueurs</div></div>
      <div class="mp-stat-box"><div class="mp-stat-val">${{st.nb_tournois}}</div><div class="mp-stat-lbl">Tournois</div></div>
      <div class="mp-stat-box"><div class="mp-stat-val">${{st.avg_echelon||'—'}}</div><div class="mp-stat-lbl">\u00c9chelon moy.</div></div>
    </div>
    <div class="mp-section">Top joueurs — clic pour voir le réseau</div>
    <div id="mp-players">
      ${{st.top_players.map(p => p.idx < 0 ? '' :
        `<div class="mp-player" onclick="startEgo(${{p.idx}})">
           <div>
             <div class="mp-player-name">${{p.label}}</div>
             <div class="mp-player-meta">${{p.nb_t}} tournois${{p.echelon ? ' · rang\u00a0'+p.echelon : ''}}</div>
           </div>
           <button class="mp-btn" onclick="event.stopPropagation();startEgo(${{p.idx}})">Réseau →</button>
         </div>`
      ).join('')}}
    </div>`;
}}

// ── EGO GRAPH ────────────────────────────────────────────────────────────────
let egoState = {{ centerIdx: -1, depth: 2, minWeight: 2 }};
let sim, linkSel, nodeSel, circleSel, labelSel, focusedNode = null;
let egoCurrent = null;
let graphSvg, graphG, graphZoom, graphW, graphH;

function initGraphView() {{
  graphW = window.innerWidth;
  graphH = window.innerHeight - 50;
  graphSvg = d3.select('#graph-canvas').attr('viewBox',[0,0,graphW,graphH]);
  graphG = graphSvg.append('g');
  graphZoom = d3.zoom().scaleExtent([.04,12]).filter(e => !e.type.includes('dblclick'))
    .on('start', () => {{ if(sim) sim.stop(); if(labelSel) labelSel.attr('display','none'); }})
    .on('zoom',  e => {{ graphG.attr('transform', e.transform); }})
    .on('end',   e => {{
      if(labelSel) labelSel.attr('display', n => e.transform.k > 0.45 ? null : 'none');
      if(sim) sim.restart();
    }});
  graphSvg.call(graphZoom);
  graphSvg.on('click', () => clearFocus());
}}

function startEgo(origIdx) {{
  egoState.centerIdx = origIdx;
  showView('graph');
  buildEgoGraph();
}}

function buildEgoGraph() {{
  if (egoState.centerIdx < 0) return;
  egoCurrent = computeEgoNetwork(egoState.centerIdx, egoState.depth, egoState.minWeight);
  const center = RAW.nodes[egoState.centerIdx];
  document.getElementById('graph-title').textContent =
    center.label + '  ·  profondeur ' + egoState.depth;
  document.getElementById('graph-stats-small').textContent =
    egoCurrent.nodes.length + ' joueurs, ' + egoCurrent.links.length + ' liens';
  rebuildGraph(egoCurrent.nodes, egoCurrent.links, egoCurrent.centerNewIdx);
}}

function rebuildGraph(nodes, links, centerIdx) {{
  if (sim) sim.stop();
  graphG.selectAll('*').remove();
  focusedNode = null;

  const localDeg = {{}};
  links.forEach(l => {{
    const si = typeof l.source==='object'?l.source.index:l.source;
    const ti = typeof l.target==='object'?l.target.index:l.target;
    if(si!==undefined) localDeg[si]=(localDeg[si]||0)+1;
    if(ti!==undefined) localDeg[ti]=(localDeg[ti]||0)+1;
  }});

  // Positions initiales : disposition radiale par profondeur
  const cx = graphW/2, cy = graphH/2;
  const G   = 2.39996;
  const byDepth = {{}};
  nodes.forEach((d,i) => {{ const dep=d._depth||0; (byDepth[dep]=byDepth[dep]||[]).push(i); }});
  nodes.forEach((d,i) => {{
    const dep = d._depth||0;
    const pos = byDepth[dep].indexOf(i);
    const n   = byDepth[dep].length;
    const r   = dep===0 ? 0 : 80 + dep*120;
    const ang = n>1 ? (pos/n)*2*Math.PI - Math.PI/2 : 0;
    d.x = cx + r*Math.cos(ang); d.y = cy + r*Math.sin(ang);
    d.vx=0; d.vy=0;
    if(!d._pinned){{ d.fx=null; d.fy=null; }}
  }});

  // Liens
  linkSel = graphG.append('g').attr('class','links').selectAll('line')
    .data(links).join('line')
    .attr('stroke',l=>linkColor(l.weight))
    .attr('stroke-opacity',l=>linkOpacity(l.weight))
    .attr('stroke-width',l=>linkWidth(l.weight))
    .attr('stroke-linecap','round');

  // Drag Obsidian
  const LS_BASE = l => Math.min(0.06, 0.003+(l.weight||1)*0.008);
  const LS_DRAG = l => Math.min(0.55, 0.08 +(l.weight||1)*0.07);
  function addPinRing(d) {{
    nodeSel.filter(n=>n===d).selectAll('.pin-ring').remove();
    nodeSel.filter(n=>n===d).append('circle')
      .attr('class','pin-ring').attr('r',nodeRadius(d)+5)
      .attr('fill','none').attr('stroke','rgba(255,255,255,.22)')
      .attr('stroke-width',1.5).attr('stroke-dasharray','4,3').attr('pointer-events','none');
  }}

  nodeSel = graphG.append('g').attr('class','nodes').selectAll('g')
    .data(nodes).join('g').attr('class','node').style('cursor','pointer')
    .call(d3.drag()
      .on('start',(e,d)=>{{ if(!e.active){{sim.force('link').strength(LS_DRAG);sim.alphaTarget(0.4).restart();}} d.fx=d.x;d.fy=d.y; }})
      .on('drag', (e,d)=>{{ d.fx=e.x; d.fy=e.y; }})
      .on('end',  (e,d)=>{{
        if(!e.active){{sim.force('link').strength(LS_BASE);sim.alphaTarget(0);}}
        d.fx=d.x; d.fy=d.y; d._pinned=true; addPinRing(d);
      }}))
    .on('click',(e,d)=>{{ e.stopPropagation(); handleNodeClick(d); }})
    .on('dblclick',(e,d)=>{{
      e.stopPropagation();
      // Double-clic = passer directement au réseau ego de ce joueur
      const idx = d._origIdx ?? RAW.nodes.findIndex(n=>n.id===d.id);
      if(idx>=0) startEgo(idx);
    }})
    .on('contextmenu',(e,d)=>{{
      e.preventDefault(); e.stopPropagation();
      d._pinned=false; d.fx=null; d.fy=null;
      nodeSel.filter(n=>n===d).selectAll('.pin-ring').remove();
      sim.alpha(0.2).restart();
    }});

  // Anneau central
  if(centerIdx>=0) {{
    nodeSel.filter((_,i)=>i===centerIdx).append('circle')
      .attr('r',nodeRadius(nodes[centerIdx])+9).attr('fill','none')
      .attr('stroke','#58a6ff').attr('stroke-width',1.8).attr('stroke-opacity',.45)
      .attr('stroke-dasharray','5,4').attr('pointer-events','none');
  }}

  circleSel = nodeSel.append('circle')
    .attr('r',d=>nodeRadius(d))
    .attr('fill',(d)=>getColor(d, d._origIdx??0))
    .attr('stroke','#0d1117').attr('stroke-width',1.5);

  labelSel = nodeSel.append('text')
    .attr('dy',d=>-nodeRadius(d)-3).attr('text-anchor','middle')
    .attr('font-size','10px').attr('fill','#e6edf3').attr('pointer-events','none')
    .style('text-shadow','0 1px 3px #000,0 1px 6px #000')
    .text(d=>d.label.length>22?d.label.slice(0,20)+'\u2026':d.label);

  function updateDOM() {{
    linkSel.attr('x1',l=>l.source.x).attr('y1',l=>l.source.y)
           .attr('x2',l=>l.target.x).attr('y2',l=>l.target.y);
    nodeSel.attr('transform',d=>`translate(${{d.x}},${{d.y}})`);
  }}

  // Force sim (ego petit → peut tourner librement)
  sim = d3.forceSimulation(nodes)
    .alphaDecay(0.015).velocityDecay(0.38).alphaMin(0.001)
    .force('link', d3.forceLink(links).id((_,i)=>i).distance(l=>55+35/Math.sqrt(l.weight||1)).strength(LS_BASE))
    .force('charge', d3.forceManyBody().strength((d,i)=>-(90+(localDeg[i]||0)*12)).distanceMax(250).distanceMin(5))
    .force('collision', d3.forceCollide().radius(d=>nodeRadius(d)+4).strength(0.8))
    .on('tick', updateDOM);

  if(centerIdx>=0){{ nodes[centerIdx].fx=cx; nodes[centerIdx].fy=cy; }}

  // Warmup collision (30 ticks rapides)
  {{
    const ws=d3.forceSimulation(nodes).alphaDecay(0.08)
      .force('col',d3.forceCollide().radius(d=>nodeRadius(d)+4).strength(1.0)).stop();
    for(let i=0;i<30;i++) ws.tick();
  }}
  updateDOM();

  document.getElementById('s-nodes').textContent = nodes.length;
  document.getElementById('s-links').textContent = links.length;
  updateLegend(colorMode);
}}

// ── Focus ─────────────────────────────────────────────────────────────────────
function handleNodeClick(d) {{
  if(focusedNode===d){{ openPlayerPanel(d); return; }}
  clearFocus(); focusedNode=d; d._focused=true;
  if(!egoCurrent) return;
  const nIds = new Set([d._origIdx]);
  egoCurrent.links.forEach(l=>{{
    if(l.source.id===d.id){{nIds.add(l.target._origIdx);l.target._neighbor=true;}}
    if(l.target.id===d.id){{nIds.add(l.source._origIdx);l.source._neighbor=true;}}
  }});
  circleSel.transition().duration(200)
    .attr('opacity',n=>nIds.has(n._origIdx)?1:0.04)
    .attr('r',n=>n===d?nodeRadius(n)*1.4:nodeRadius(n));
  labelSel.transition().duration(200).attr('opacity',n=>nIds.has(n._origIdx)?1:0).attr('display',null);
  linkSel.transition().duration(200)
    .attr('stroke',l=>l.source.id===d.id||l.target.id===d.id?linkColor(l.weight):'#21262d')
    .attr('stroke-opacity',l=>l.source.id===d.id||l.target.id===d.id?Math.min(1,linkOpacity(l.weight)*1.5):0.03)
    .attr('stroke-width',l=>l.source.id===d.id||l.target.id===d.id?linkWidth(l.weight)*1.5:0.4);
  nodeSel.filter(n=>n===d).append('circle').attr('class','focus-ring')
    .attr('r',nodeRadius(d)+6).attr('fill','none').attr('stroke','#58a6ff')
    .attr('stroke-width',2).attr('stroke-opacity',.7).attr('pointer-events','none');
  openPlayerPanel(d);
}}

function clearFocus() {{
  if(!focusedNode) return;
  focusedNode._focused=false;
  if(egoCurrent) egoCurrent.nodes.forEach(n=>n._neighbor=false);
  focusedNode=null;
  if(!circleSel) return;
  circleSel.transition().duration(200).attr('opacity',1).attr('r',d=>nodeRadius(d));
  labelSel.transition().duration(200).attr('opacity',1);
  linkSel.transition().duration(200)
    .attr('stroke',l=>linkColor(l.weight)).attr('stroke-opacity',l=>linkOpacity(l.weight))
    .attr('stroke-width',l=>linkWidth(l.weight));
  nodeSel.selectAll('.focus-ring').remove();
  document.getElementById('player-panel').classList.remove('open');
}}

// ── Player panel ──────────────────────────────────────────────────────────────
function openPlayerPanel(d) {{
  document.getElementById('pp-name').textContent = d.label;
  document.getElementById('pp-sub').textContent  = [d.club||'',d.sexe||''].filter(Boolean).join(' · ');
  document.getElementById('pp-tournois').textContent = d.nb_tournois;
  document.getElementById('pp-echelon').textContent  = d.echelon||'NC';
  document.getElementById('pp-ville').textContent    = d.club||'—';
  document.getElementById('pp-region').textContent   = (ALL_REGIONS.find(r=>r.code===d.region)||{{}}).label||'—';

  const partners=[];
  if(egoCurrent) {{
    egoCurrent.links.forEach(l=>{{
      if(l.source.id===d.id) partners.push({{node:l.target,w:l.weight}});
      if(l.target.id===d.id) partners.push({{node:l.source,w:l.weight}});
    }});
  }}
  partners.sort((a,b)=>b.w-a.w);
  document.getElementById('pp-npartners').textContent = partners.length;
  document.getElementById('pp-partners').innerHTML = partners.slice(0,30).map(p=>{{
    const idx = p.node._origIdx ?? RAW.nodes.findIndex(n=>n.id===p.node.id);
    return `<div class="pppartner">
      <span>${{p.node.label}}</span>
      <div style="display:flex;align-items:center;gap:4px">
        <span class="pppartner-cnt">${{p.w}}x</span>
        <button class="pp-goto-btn" onclick="startEgo(${{idx}})">→</button>
      </div>
    </div>`;
  }}).join('')||'<div style="color:#484f58;font-size:11px;padding-top:8px">Aucun partenaire visible</div>';

  document.getElementById('player-panel').classList.add('open');
}}
document.getElementById('pp-close').onclick = ()=>clearFocus();

// ── Légende ───────────────────────────────────────────────────────────────────
function updateLegend(mode) {{
  const el = document.getElementById('graph-legend');
  const dot = (c,l) => `<div class="legend-item"><div class="legend-dot" style="background:${{c}}"></div><span>${{l}}</span></div>`;
  const bar = (g,l0,l1) => `<span style="font-size:10px;color:#8b949e">${{l0}}</span><div class="legend-bar" style="background:linear-gradient(to right,${{g}})"></div><span style="font-size:10px;color:#8b949e">${{l1}}</span>`;

  const legends={{
    niveau: [dot('#484f58','Non class\u00e9')].concat(NIVEAU_TIERS.map(([,,c,l])=>dot(c,l))),
    sexe:   [dot('#58a6ff','Homme'),dot('#db61a2','Femme'),dot('#484f58','NC')],
    region: ALL_REGIONS.map(r=>dot(r.color,r.label)).concat([dot('#484f58','Non mapp\u00e9')]),
    dept:   ALL_DEPTS.slice(0,18).map(d=>dot(DEPT_COLOR_FINE[d]||DEPT_PALETTE[d]||'#484f58',d))
              .concat(ALL_DEPTS.length>18?[`<span style="color:#484f58;font-size:10px">\u2026${{ALL_DEPTS.length-18}} autres</span>`]:[]),
    club:   TOP_VILLES.map((v,i)=>dot(VILLE_PALETTE[i%VILLE_PALETTE.length],v)).concat([dot('#484f58','Autres')]),
    activite:   [bar('#2d333b,#fdae61,#f46d43,#a50026','Peu actif','Très actif')],
    connexions: [bar('#2d333b,#4575b4,#74add1,#abd9e9,#fee090','Peu connecté','Hub')],
  }};
  el.innerHTML=(legends[mode]||[]).join('');
}}

// ── View toggle ───────────────────────────────────────────────────────────────
function showView(name) {{
  document.getElementById('map-view').classList.toggle('hidden', name!=='map');
  document.getElementById('graph-view').classList.toggle('active', name==='graph');
  document.getElementById('tab-map').classList.toggle('active',   name==='map');
  document.getElementById('tab-graph').classList.toggle('active', name==='graph');
}}
document.getElementById('tab-map').onclick   = ()=>showView('map');
document.getElementById('tab-graph').onclick = ()=>{{
  if(egoState.centerIdx<0){{
    alert('Recherchez d\u2019abord un joueur pour voir son r\u00e9seau.');
    return;
  }}
  showView('graph');
}};

// ── Search avec dropdown ──────────────────────────────────────────────────────
const searchInput = document.getElementById('search-input');
const dropdown    = document.getElementById('search-dropdown');

function posDropdown() {{
  const r = searchInput.getBoundingClientRect();
  dropdown.style.left  = r.left+'px';
  dropdown.style.top   = r.bottom+'px';
  dropdown.style.width = r.width+'px';
}}
searchInput.addEventListener('input', function() {{
  const q = this.value.toLowerCase().trim();
  dropdown.innerHTML=''; dropdown.style.display='none';
  if(!q||q.length<2) return;
  const hits = RAW.nodes.map((n,i)=>({{n,i}})).filter(x=>x.n.label.toLowerCase().includes(q)).slice(0,12);
  if(!hits.length) return;
  posDropdown(); dropdown.style.display='block';
  hits.forEach(({{n,i}})=>{{
    const item=document.createElement('div');
    item.className='search-item';
    item.textContent=n.label+(n.club?' · '+n.club:'');
    item.onclick=()=>{{ searchInput.value=n.label; dropdown.style.display='none'; startEgo(i); }};
    dropdown.appendChild(item);
  }});
}});
document.addEventListener('click',e=>{{ if(e.target!==searchInput) dropdown.style.display='none'; }});

// ── Depth & weight sliders ────────────────────────────────────────────────────
document.getElementById('depth-slider').addEventListener('input', function(){{
  egoState.depth=parseInt(this.value);
  document.getElementById('depth-val').textContent=this.value;
  if(egoState.centerIdx>=0) buildEgoGraph();
}});
document.getElementById('min-weight').addEventListener('input', function(){{
  egoState.minWeight=parseInt(this.value);
  document.getElementById('mw-val').textContent=this.value;
  if(egoState.centerIdx>=0) buildEgoGraph();
}});

// ── Color mode ────────────────────────────────────────────────────────────────
document.querySelectorAll('.color-btn').forEach(btn=>{{
  btn.addEventListener('click',()=>{{
    document.querySelectorAll('.color-btn').forEach(b=>b.classList.remove('active'));
    btn.classList.add('active');
    colorMode=btn.dataset.mode;
    if(circleSel) circleSel.transition().duration(300).attr('fill',d=>getColor(d,d._origIdx??0));
    updateLegend(colorMode);
  }});
}});

// ── Map metric ────────────────────────────────────────────────────────────────
document.querySelectorAll('.metric-btn').forEach(btn=>{{
  btn.addEventListener('click',()=>{{
    document.querySelectorAll('.metric-btn').forEach(b=>b.classList.remove('active'));
    btn.classList.add('active');
    mapMetric=btn.dataset.metric;
    renderMap();
  }});
}});

// ── Shake / Reset ─────────────────────────────────────────────────────────────
document.getElementById('btn-shake').onclick=()=>{{
  if(!sim||!egoCurrent) return;
  egoCurrent.nodes.forEach(d=>{{
    if(!d._pinned){{d.fx=null;d.fy=null;}}
    d.vx=(Math.random()-.5)*60; d.vy=(Math.random()-.5)*60;
  }});
  sim.alpha(0.5).alphaTarget(0).restart();
}};
document.getElementById('btn-reset').onclick=()=>{{
  if(!egoCurrent) return;
  egoCurrent.nodes.forEach(d=>{{d._pinned=false;d.fx=null;d.fy=null;}});
  if(nodeSel) nodeSel.selectAll('.pin-ring').remove();
  clearFocus(); buildEgoGraph();
}};

// ── Escape ────────────────────────────────────────────────────────────────────
document.addEventListener('keydown', e=>{{ if(e.key==='Escape') clearFocus(); }});

// ── Init ──────────────────────────────────────────────────────────────────────
updateLegend('niveau');
initMap();
initGraphView();
</script>
</body>
</html>"""

with open(OUT_FILE, 'w', encoding='utf-8') as f:
    f.write(html)

print(f"✅  Graphe généré : {OUT_FILE}")
print(f"    Ouvre ce fichier dans ton navigateur")
