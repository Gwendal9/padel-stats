"""
Reconstruction d'un tournoi à partir des participations.

Étape 1 : audit_positions()       → liste les libellés de 'position' dans la DB
Étape 2 : reconstruct(id_tournoi) → groupe les paires par tour atteint
Étape 3 : render_html(...)        → génère un fichier HTML visualisable

Usage :
    python reconstruct_tournoi.py audit
    python reconstruct_tournoi.py top                   # liste les plus gros tournois
    python reconstruct_tournoi.py reconstruct <id_tournoi>
    python reconstruct_tournoi.py demo                  # auto : prend un gros tournoi et le reconstruit
"""
import sqlite3
import sys
import os
import re
import json
from collections import defaultdict
from points_tables import POINTS, bracket_structure, bracket_index, expected_points

DB = os.path.join(os.path.dirname(__file__), 'tenup.db')

# ─── Mapping des positions ─────────────────────────────────────────────────
# La FFT stocke la position FINALE de la paire dans le tournoi sous forme
# d'un entier : 1 = vainqueur, 2 = finaliste, 3-4 = demi, 5-8 = 1/4, etc.
POSITION_ORDER = [
    'Vainqueur', 'Finaliste',
    '1/2 finale', '1/4 finale', '1/8 finale',
    '1/16 finale', '1/32 finale', '1/64 finale',
    'Phase préliminaire', 'Forfait', 'Inconnu',
]

def normalize_position(raw):
    """Mappe la position (entier ou texte) vers un tour canonique."""
    if raw is None or raw == '':
        return 'Inconnu'
    s = str(raw).strip()

    # Cas principal : position numérique (1 = vainqueur, etc.)
    try:
        n = int(s)
        if   n == 1:           return 'Vainqueur'
        elif n == 2:           return 'Finaliste'
        elif n <= 4:           return '1/2 finale'
        elif n <= 8:           return '1/4 finale'
        elif n <= 16:          return '1/8 finale'
        elif n <= 32:          return '1/16 finale'
        elif n <= 64:          return '1/32 finale'
        elif n <= 128:         return '1/64 finale'
        else:                  return 'Phase préliminaire'
    except (ValueError, TypeError):
        pass

    # Fallback texte (libellés interclubs, forfaits, etc.)
    sl = s.lower()
    if any(k in sl for k in ['vainqueur','champion','gagnant','winner']): return 'Vainqueur'
    if any(k in sl for k in ['finaliste','finale']):                       return 'Finaliste'
    if re.search(r'1/2|demi|semi|½', sl):                                  return '1/2 finale'
    if re.search(r'1/4|quart|¼',     sl):                                  return '1/4 finale'
    if re.search(r'1/8|8[eè]me?',    sl):                                  return '1/8 finale'
    if re.search(r'1/16|16[eè]me?',  sl):                                  return '1/16 finale'
    if re.search(r'1/32|32[eè]me?',  sl):                                  return '1/32 finale'
    if 'forfait' in sl or 'wo' in sl:                                       return 'Forfait'
    if 'poule' in sl or 'groupe' in sl:                                     return 'Phase préliminaire'
    return 'Inconnu'

# ─── Détection des tournois "interclubs" / par équipes ───────────────────
INTERCLUBS_KEYWORDS = [
    'interclub', 'inter-club', 'inter club',
    'championnat', 'chpt', 'epreuve par equipe', 'épreuve par équipe',
    'par équipes', 'par equipes',
]
def is_interclubs(nom, categorie):
    """True si le tournoi a un format par équipes (et pas tournoi individuel par paires)."""
    blob = f"{(nom or '').lower()} {(categorie or '').lower()}"
    return any(k in blob for k in INTERCLUBS_KEYWORDS)

# ───────────────────────────────────────────────────────────────────────────

def conn_ro():
    """Connexion read-only à la base."""
    return sqlite3.connect(DB)

def audit_positions():
    """Affiche tous les libellés de 'position' triés par fréquence."""
    conn = conn_ro()
    print(f"\n━━━ Distribution brute des positions (top 30) ━━━\n")
    rows = conn.execute("""
        SELECT position, COUNT(*) as n
        FROM participations
        GROUP BY position
        ORDER BY n DESC
    """).fetchall()
    print(f"  {'Position brute':25s}  {'Normalisée':20s}  Count")
    print(f"  {'─'*25}  {'─'*20}  ─────")
    for raw, n in rows[:30]:
        canon = normalize_position(raw)
        raw_disp = (raw if raw not in (None, '') else '(vide)').strip()[:23]
        print(f"  {raw_disp:25s}  {canon:20s}  {n:>10,}")

    if len(rows) > 30:
        print(f"  ... et {len(rows)-30} autres libellés moins fréquents")

    print(f"\n━━━ Récapitulatif normalisé ━━━\n")
    counts = defaultdict(int)
    for raw, n in rows:
        counts[normalize_position(raw)] += n
    total = sum(counts.values())
    for canon in POSITION_ORDER:
        c = counts.get(canon, 0)
        pct = (c/total*100) if total else 0
        bar = '█' * int(pct/2)
        print(f"  {canon:20s}  {c:>10,}  {pct:5.1f}%  {bar}")
    conn.close()

def top_tournois(limit=15, exclude_interclubs=True):
    """Liste les tournois avec le plus de participations enregistrées."""
    conn = conn_ro()
    rows = conn.execute("""
        SELECT t.id_tournoi, t.nom, t.categorie, COUNT(p.id) as n_part
        FROM tournois t
        LEFT JOIN participations p ON p.id_tournoi = t.id_tournoi
        GROUP BY t.id_tournoi
        HAVING n_part >= 8
        ORDER BY n_part DESC
        LIMIT ?
    """, (limit * 5,)).fetchall()  # on prend large pour pouvoir filtrer
    if exclude_interclubs:
        rows = [r for r in rows if not is_interclubs(r[1], r[2])][:limit]
    else:
        rows = rows[:limit]
    label = "tournois individuels" if exclude_interclubs else "tournois (tous types)"
    print(f"\n━━━ Top {limit} {label} ━━━\n")
    print(f"  {'ID':>12}  {'Nom':45s}  {'Catégorie':18s}  N parts  Type")
    print(f"  {'─'*12}  {'─'*45}  {'─'*18}  ───────  ────")
    for r in rows:
        type_t = 'interclubs' if is_interclubs(r[1], r[2]) else 'tournoi'
        print(f"  {r[0]:>12}  {(r[1] or '')[:45]:45s}  {(r[2] or '')[:18]:18s}  {r[3]:>7}  {type_t}")
    conn.close()
    return rows

def reconstruct(id_tournoi, verbose=True):
    """
    Reconstruit un tournoi : déduplique les paires + groupe par tour atteint.
    Retourne un dict structuré :
        {
            'id_tournoi': str,
            'nom': str, 'categorie': str,
            'date': str,
            'rounds': {   # clé = position canonique
                'Vainqueur': [{ pair_id, joueurs: [{id,nom,prenom,classement}, ...], points }],
                ...
            },
            'pairs': [...]  # liste plate de toutes les paires
        }
    """
    conn = conn_ro()
    # Métadonnées
    meta = conn.execute("SELECT nom, categorie FROM tournois WHERE id_tournoi=?", (id_tournoi,)).fetchone()
    nom, categorie = meta if meta else (None, None)

    # Toutes les participations du tournoi
    parts = conn.execute("""
        SELECT p.id_joueur, p.partenaire_id, p.partenaire_nom, p.date_tournoi,
               p.position, p.points, p.type,
               j.nom, j.prenom, j.classement, j.club_nom
        FROM participations p
        LEFT JOIN joueurs j ON j.id_fft = p.id_joueur
        WHERE p.id_tournoi = ?
    """, (id_tournoi,)).fetchall()
    conn.close()

    if not parts:
        return None

    # On déduplique les paires : clé = tuple(min(A,B), max(A,B))
    # Si partenaire_id manque, on utilise le nom
    pair_map = {}  # key → {position, points, joueurs:[...]}
    for p in parts:
        (jid, pid, pnom, date, pos, points, typ, nom_j, prenom_j, cls, club) = p
        # Construction d'une clé de paire
        if pid:
            key = tuple(sorted([str(jid), str(pid)]))
        else:
            # fallback : on garde la paire indexée par le partenaire_nom
            key = (str(jid), pnom or '?')
        canonical_pos = normalize_position(pos)

        if key not in pair_map:
            pair_map[key] = {
                'pair_key': key, 'position_raw': pos, 'position': canonical_pos,
                'date': date, 'points': points, 'type': typ,
                'joueurs': []
            }
        # Ajoute le joueur s'il n'est pas déjà dans la paire
        existing_ids = [j['id'] for j in pair_map[key]['joueurs']]
        if jid and jid not in existing_ids:
            pair_map[key]['joueurs'].append({
                'id': jid, 'nom': nom_j, 'prenom': prenom_j,
                'classement': cls, 'club': club
            })
        # Si on a un partenaire_nom mais pas en base, on l'ajoute en "fantôme"
        if pid and pid != jid and not any(j['id'] == pid for j in pair_map[key]['joueurs']):
            # On essaiera de récupérer ses infos
            partner_info = None
            with sqlite3.connect(DB) as c2:
                row = c2.execute(
                    "SELECT nom, prenom, classement, club_nom FROM joueurs WHERE id_fft=?",
                    (pid,)
                ).fetchone()
                if row:
                    partner_info = {'id': pid, 'nom': row[0], 'prenom': row[1],
                                    'classement': row[2], 'club': row[3]}
            if not partner_info:
                # Pas en base : on parse le nom
                partner_info = {'id': pid, 'nom': pnom or '?', 'prenom': '',
                                'classement': None, 'club': None}
            pair_map[key]['joueurs'].append(partner_info)

    pairs = list(pair_map.values())

    # Groupe par position canonique
    rounds = defaultdict(list)
    for pair in pairs:
        rounds[pair['position']].append(pair)

    result = {
        'id_tournoi': id_tournoi,
        'nom': nom, 'categorie': categorie,
        'date': pairs[0]['date'] if pairs else None,
        'n_pairs': len(pairs),
        'rounds': dict(rounds),
        'pairs': pairs,
    }

    if verbose:
        print(f"\n━━━ Tournoi {id_tournoi} ━━━")
        print(f"   Nom       : {nom}")
        print(f"   Catégorie : {categorie}")
        print(f"   Type      : {'INTERCLUBS / par équipes' if is_interclubs(nom, categorie) else 'Tournoi individuel'}")
        print(f"   Paires    : {len(pairs)}")
        print(f"\n   Pyramide :")
        for canon in POSITION_ORDER:
            ps = rounds.get(canon, [])
            if not ps: continue
            # Trier les paires par position brute (numérique si possible)
            ps_sorted = sorted(ps, key=lambda x: int(x['position_raw']) if str(x['position_raw']).isdigit() else 999)
            print(f"     {canon:20s} ({len(ps)} paire{'s' if len(ps)>1 else ''})")
            for p in ps_sorted[:6]:
                noms = ' / '.join(f"{j['prenom'] or ''} {j['nom'] or '?'}".strip()
                                  for j in p['joueurs'][:2])
                cls = ' & '.join(f"#{j['classement']}" if j['classement'] else '#?'
                                 for j in p['joueurs'][:2])
                pos_n = p['position_raw']
                print(f"        [pos {pos_n}] {noms}  ({cls}) — {p['points'] or 0} pts")
            if len(ps) > 6:
                print(f"        ... et {len(ps)-6} autres paires")

    return result

def render_html(data, out='tournoi_view.html'):
    """Génère un HTML compact visualisable du tournoi."""
    if not data:
        print("❌ Pas de données à rendre")
        return

    rounds_html = ''
    bg_colors = {
        'Vainqueur':           'from-amber-50 to-amber-100/50 border-amber-200',
        'Finaliste':           'from-slate-50 to-slate-100 border-slate-200',
        '1/2 finale':          'from-orange-50 to-orange-100/50 border-orange-200',
        '1/4 finale':          'from-blue-50 to-blue-100/50 border-blue-200',
        '1/8 finale':          'from-cyan-50 to-cyan-100/50 border-cyan-200',
        '1/16 finale':         'from-teal-50 to-teal-100/50 border-teal-200',
        '1/32 finale':         'from-emerald-50 to-emerald-100/50 border-emerald-200',
        '1/64 finale':         'from-green-50 to-green-100/50 border-green-200',
        'Phase préliminaire':  'from-purple-50 to-purple-100/50 border-purple-200',
        'Forfait':             'from-red-50 to-red-100/50 border-red-200',
        'Inconnu':             'from-slate-50 to-slate-100 border-slate-200',
    }
    badges = {
        'Vainqueur': '🥇', 'Finaliste': '🥈', '1/2 finale': '🥉',
        '1/4 finale': '🎯', '1/8 finale': '🎾', '1/16 finale': '🎾',
        '1/32 finale': '🎾', '1/64 finale': '🎾',
        'Phase préliminaire': '🔷', 'Forfait': '⚠️', 'Inconnu': '❓',
    }

    for canon in POSITION_ORDER:
        ps = data['rounds'].get(canon, [])
        if not ps: continue
        # Tri par position numérique pour ordre correct
        ps = sorted(ps, key=lambda x: int(x['position_raw']) if str(x['position_raw']).isdigit() else 999)
        bg = bg_colors.get(canon, 'from-slate-50 to-slate-100 border-slate-200')
        emoji = badges.get(canon, '')
        rounds_html += f'<div class="mb-6"><div class="text-sm font-semibold text-slate-700 mb-3 flex items-center gap-2"><span class="text-xl">{emoji}</span>{canon} <span class="text-xs font-normal text-slate-500">({len(ps)} paire{"s" if len(ps)>1 else ""})</span></div>'
        rounds_html += '<div class="grid grid-cols-2 lg:grid-cols-3 gap-3">'
        for p in ps:
            joueurs = p['joueurs'][:2]
            while len(joueurs) < 2:
                joueurs.append({'nom': '?', 'prenom': '', 'classement': None, 'club': None})
            j1, j2 = joueurs[0], joueurs[1]
            classements = [f"#{j['classement']}" if j['classement'] else '#?' for j in joueurs]
            rounds_html += f'''
            <div class="rounded-xl border bg-gradient-to-br {bg} p-3 relative">
                <div class="absolute top-2 right-2 text-[10px] font-mono bg-white/70 px-1.5 py-0.5 rounded text-slate-500">#{p['position_raw']}</div>
                <div class="flex items-center gap-2 text-sm font-medium">
                    <span>{(j1['prenom'] or '').strip()} {j1['nom'] or '?'}</span>
                    <span class="ml-auto text-xs text-slate-500">{classements[0]}</span>
                </div>
                <div class="flex items-center gap-2 text-sm font-medium">
                    <span>{(j2['prenom'] or '').strip()} {j2['nom'] or '?'}</span>
                    <span class="ml-auto text-xs text-slate-500">{classements[1]}</span>
                </div>
                <div class="mt-2 pt-2 border-t border-white/60 flex justify-between text-xs text-slate-600">
                    <span class="truncate">{j1['club'] or ''}</span>
                    <span class="font-semibold whitespace-nowrap ml-2">+{p['points'] or 0} pts</span>
                </div>
            </div>'''
        rounds_html += '</div></div>'

    html = f'''<!DOCTYPE html>
<html lang="fr"><head>
<meta charset="UTF-8">
<title>Tournoi {data['id_tournoi']}</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>* {{ font-family: 'Inter', system-ui, sans-serif; }}</style>
</head>
<body class="bg-slate-50 p-8">
<div class="max-w-5xl mx-auto">
  <div class="bg-gradient-to-br from-rose-500 via-pink-600 to-purple-700 rounded-2xl p-6 text-white mb-6">
    <div class="text-xs uppercase tracking-widest opacity-75 mb-1">Tournoi reconstruit</div>
    <div class="text-3xl font-bold">{data['nom'] or 'Sans nom'}</div>
    <div class="flex gap-4 text-sm opacity-90 mt-2">
      <span>🏷️ {data['categorie'] or '?'}</span>
      <span>📅 {data['date'] or '?'}</span>
      <span>👥 {data['n_pairs']} paires</span>
      <span>🆔 {data['id_tournoi']}</span>
    </div>
  </div>
  {rounds_html}
</div>
</body></html>
'''
    out_path = os.path.join(os.path.dirname(__file__), out)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"\n📄 HTML généré → {out_path}")
    print(f"   Ouvre-le dans ton navigateur pour voir le rendu visuel.")
    return out_path

# ─── Bracket horizontal (vue arbre type image) ────────────────────────────
def render_bracket_html(data, out='tournoi_bracket.html'):
    """
    Génère un bracket horizontal style image FFT :
        - colonnes = tours, de gauche (1ère phase) à droite (vainqueur)
        - chaque colonne contient les paires éliminées à ce tour
        - lignes connecteurs entre colonnes
    """
    if not data:
        print("❌ Pas de données à rendre")
        return

    n_pairs = data['n_pairs']
    structure = bracket_structure(n_pairs)
    # On compte combien de paires sont éliminées à chaque tour :
    # à un tour qui démarre avec K paires, K/2 sont éliminées
    # Sauf le dernier tour (Vainqueur) qui n'a qu'1 paire (le gagnant)

    # Mapping nom_tour → liste des paires (déjà groupées dans data['rounds'])
    rounds = data['rounds']

    def pair_card(p, color_class='', size='normal'):
        """Génère le HTML d'une carte paire."""
        joueurs = p['joueurs'][:2]
        while len(joueurs) < 2:
            joueurs.append({'nom': '?', 'prenom': '', 'classement': None, 'club': None})
        j1, j2 = joueurs[0], joueurs[1]
        cls_size = 'text-xs' if size == 'small' else 'text-sm'
        return f'''
        <div class="bracket-card {color_class}">
            <div class="pos-tag">#{p['position_raw']}</div>
            <div class="{cls_size} font-medium truncate">{(j1['prenom'] or '').strip()} {j1['nom'] or '?'}</div>
            <div class="{cls_size} font-medium truncate">{(j2['prenom'] or '').strip()} {j2['nom'] or '?'}</div>
            <div class="text-[10px] text-slate-500 mt-1">#{j1['classement'] or '?'} · #{j2['classement'] or '?'} · +{p['points'] or 0}pts</div>
        </div>'''

    # Couleurs par tour
    color_map = {
        'Vainqueur':    'card-winner',
        'Finaliste':    'card-finalist',
        '1/2 finale':   'card-semi',
        '1/4 finale':   'card-quarter',
        '1/8 finale':   'card-r16',
        '1/16 finale':  'card-r32',
        '1/32 finale':  'card-r64',
    }

    # Construire les colonnes de gauche (1ère phase) à droite (vainqueur)
    # Pour la première colonne : on affiche toutes les paires ENTRÉES dans le bracket
    # Mais la FFT ne nous donne que les positions FINALES, donc on affiche uniquement
    # les paires qui ont été éliminées à chaque tour (= sont placées dans ce tour).
    columns_html = ''
    for round_name, _starting_pairs in structure:
        ps = rounds.get(round_name, [])
        if not ps and round_name != 'Vainqueur': continue
        ps = sorted(ps, key=lambda x: int(x['position_raw']) if str(x['position_raw']).isdigit() else 999)
        color = color_map.get(round_name, '')
        emoji = {
            'Vainqueur': '🏆', 'Finaliste': '🥈', '1/2 finale': '🥉',
            '1/4 finale': '🎯', '1/8 finale': '🎾', '1/16 finale': '🎾',
            '1/32 finale': '🎾',
        }.get(round_name, '◾')
        columns_html += f'''
        <div class="bracket-col" style="--n: {max(len(ps), 1)}">
            <div class="col-header"><span class="text-xl">{emoji}</span> {round_name}<span class="text-xs text-slate-400 ml-1">({len(ps)})</span></div>
            <div class="col-cards">'''
        for p in ps:
            columns_html += pair_card(p, color)
        columns_html += '</div></div>'

    # Calcul stats
    stats_html = ''
    for round_name, _ in structure:
        ps = rounds.get(round_name, [])
        if ps:
            stats_html += f'<span class="stat-pill">{round_name}: <b>{len(ps)}</b></span>'

    is_inter = is_interclubs(data['nom'], data['categorie'])
    type_label = '🏢 Interclubs / Par équipes' if is_inter else '🎾 Tournoi individuel'

    html = f'''<!DOCTYPE html>
<html lang="fr"><head>
<meta charset="UTF-8"><title>Bracket — {data['nom']}</title>
<script src="https://cdn.tailwindcss.com"></script>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  * {{ font-family: 'Inter', system-ui, sans-serif; }}
  body {{ background: #f1f5f9; min-height: 100vh; padding: 24px; }}
  .bracket-wrap {{
    display: flex; gap: 12px; align-items: stretch;
    overflow-x: auto; padding: 12px;
    background: white; border-radius: 16px;
    border: 1px solid #e2e8f0;
    min-height: 500px;
  }}
  .bracket-col {{
    display: flex; flex-direction: column;
    min-width: 220px;
  }}
  .col-header {{
    font-size: 13px; font-weight: 600; color: #334155;
    padding: 8px 12px; border-bottom: 2px solid #e2e8f0;
    margin-bottom: 12px; display: flex; align-items: center; gap: 6px;
  }}
  .col-cards {{
    display: flex; flex-direction: column; gap: 8px;
    flex: 1; justify-content: space-around;
  }}
  .bracket-card {{
    background: white; border: 1px solid #e2e8f0; border-radius: 8px;
    padding: 10px; position: relative;
    transition: all 0.15s; box-shadow: 0 1px 2px rgba(0,0,0,0.04);
  }}
  .bracket-card:hover {{ transform: translateY(-1px); box-shadow: 0 4px 8px rgba(0,0,0,0.08); border-color: #94a3b8; }}
  .pos-tag {{
    position: absolute; top: 6px; right: 6px;
    font-size: 10px; font-family: ui-monospace, monospace;
    background: #f1f5f9; color: #64748b; padding: 1px 5px; border-radius: 4px;
  }}
  /* Couleurs par tour */
  .card-winner   {{ background: linear-gradient(135deg,#fef3c7,#fde68a); border-color: #f59e0b; }}
  .card-winner .pos-tag {{ background: #f59e0b; color: white; }}
  .card-finalist {{ background: #f1f5f9; border-color: #94a3b8; }}
  .card-semi     {{ background: linear-gradient(135deg,#ffedd5,#fed7aa); border-color: #fb923c; }}
  .card-quarter  {{ background: linear-gradient(135deg,#dbeafe,#bfdbfe); border-color: #60a5fa; }}
  .card-r16      {{ background: linear-gradient(135deg,#cffafe,#a5f3fc); border-color: #22d3ee; }}
  .card-r32      {{ background: linear-gradient(135deg,#ccfbf1,#99f6e4); border-color: #2dd4bf; }}
  .card-r64      {{ background: linear-gradient(135deg,#dcfce7,#bbf7d0); border-color: #4ade80; }}

  .stat-pill {{
    display: inline-block; padding: 4px 10px; margin: 2px;
    background: #f1f5f9; border-radius: 999px; font-size: 12px;
  }}
</style>
</head>
<body>
<div class="max-w-[1600px] mx-auto">
  <div class="bg-gradient-to-br from-rose-500 via-pink-600 to-purple-700 rounded-2xl p-6 text-white mb-4">
    <div class="text-xs uppercase tracking-widest opacity-75 mb-1">Bracket reconstruit</div>
    <div class="text-3xl font-bold">{data['nom'] or 'Sans nom'}</div>
    <div class="flex flex-wrap gap-3 text-sm opacity-90 mt-2">
      <span>🏷️ {data['categorie'] or '?'}</span>
      <span>📅 {data['date'] or '?'}</span>
      <span>👥 {n_pairs} paires</span>
      <span>🆔 {data['id_tournoi']}</span>
      <span>{type_label}</span>
    </div>
  </div>
  <div class="bg-white rounded-xl p-4 mb-4 border border-slate-200">
    <div class="text-xs text-slate-500 uppercase font-semibold tracking-wider mb-2">Phases atteintes</div>
    <div>{stats_html}</div>
  </div>
  <div class="bracket-wrap">{columns_html}</div>
  <div class="text-xs text-slate-500 mt-2">
    💡 Le bracket affiche les paires éliminées à chaque tour, dans l'ordre du classement final FFT.
    Les confrontations directes (qui a battu qui) ne sont pas dans la base, donc on visualise la pyramide des résultats plutôt qu'un arbre exact.
  </div>
</div>
</body></html>'''
    out_path = os.path.join(os.path.dirname(__file__), out)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"\n🎯 Bracket HTML → {out_path}")
    return out_path

# ─── VRAI bracket arbre avec lignes connectrices (style image FFT) ────────
def seeded_order(size):
    """
    Génère l'ordre des seeds dans un bracket à élimination directe.
    Ex pour size=8 : [1, 8, 4, 5, 3, 6, 2, 7]
    Pour size=16 : [1, 16, 8, 9, 5, 12, 4, 13, 3, 14, 6, 11, 7, 10, 2, 15]
    """
    order = [1, 2]
    while len(order) < size:
        new_order = []
        sum_seeds = len(order) * 2 + 1
        for s in order:
            new_order.extend([s, sum_seeds - s])
        order = new_order
    return order

def render_tree_html(data, out='tournoi_tree.html'):
    """Bracket arbre type image FFT avec lignes connectrices SVG."""
    if not data:
        print("❌ Pas de données à rendre")
        return

    rounds = data['rounds']
    n_pairs = data['n_pairs']

    # Détermine la taille du bracket (puissance de 2 immédiatement supérieure)
    bracket_size = 1
    while bracket_size < n_pairs: bracket_size *= 2
    bracket_size = max(bracket_size, 4)

    # Toutes les paires triées par position FFT (1 = vainqueur, etc.)
    all_pairs = []
    for round_name, ps in rounds.items():
        for p in ps:
            try: rang = int(p['position_raw'])
            except: rang = 999
            all_pairs.append((rang, p))
    all_pairs.sort(key=lambda x: x[0])
    sorted_pairs = [p for _, p in all_pairs]

    # On place les paires aux slots via le seeding standard
    # seeded_order(8) = [1,8,4,5,3,6,2,7] → slot 0 = pair de rang 1, slot 1 = pair de rang 8, etc.
    seed_layout = seeded_order(bracket_size)
    slots = [None] * bracket_size  # slot_idx → paire ou None
    rang_to_pair = {}
    for p in sorted_pairs:
        try: rang_to_pair[int(p['position_raw'])] = p
        except: pass
    for slot_i, seed_rang in enumerate(seed_layout):
        slots[slot_i] = rang_to_pair.get(seed_rang)

    # Géométrie du bracket
    n_rounds = 0
    s = bracket_size
    while s > 1:
        s //= 2; n_rounds += 1
    n_rounds += 1  # +1 pour la colonne "Champion"

    CARD_W = 200
    CARD_H = 56
    GAP_X = 80          # espace horizontal entre colonnes
    GAP_Y_INITIAL = 12  # espace vertical entre cartes en colonne 0
    PAD = 30

    total_h = bracket_size * (CARD_H + GAP_Y_INITIAL)
    total_w = n_rounds * (CARD_W + GAP_X) + PAD * 2

    def slot_y(round_idx, slot_in_round):
        """Position Y du centre d'une carte (round_idx, slot_in_round)."""
        # En colonne k, les cartes sont espacées de 2^k * (CARD_H + GAP_Y_INITIAL)
        slot_height = (CARD_H + GAP_Y_INITIAL) * (2 ** round_idx)
        return PAD + slot_in_round * slot_height + slot_height / 2

    def slot_x(round_idx):
        return PAD + round_idx * (CARD_W + GAP_X)

    # Construire le bracket par tour : pairs_at_round[round_idx] = liste des paires
    # En round 0 (1ère phase) : tous les slots remplis (les "entrants")
    # En round k : on met les paires qui ont gagné le round k-1 (= qui ont une rang
    # de paire telle qu'elles ont avancé à ce tour ou plus loin).
    # Avec seeded layout, le "gagnant" du match (slot 2i, 2i+1) en round k est le slot
    # de rang plus petit (= meilleur seed).
    bracket_state = [list(slots)]  # round 0
    for r in range(n_rounds - 1):
        prev = bracket_state[-1]
        new_round = []
        for i in range(0, len(prev), 2):
            a, b = prev[i], prev[i+1] if i+1 < len(prev) else None
            # Le gagnant du match = celui des deux qui a la meilleure position finale
            # (puisque la pair la mieux classée a forcément avancé plus loin)
            def rang(p):
                try: return int(p['position_raw']) if p else 999
                except: return 999
            winner = a if rang(a) < rang(b) else b
            new_round.append(winner)
        bracket_state.append(new_round)

    # SVG : génère cartes + lignes connectrices
    cards_svg = ''
    lines_svg = ''
    color_by_round = {  # plus on est à droite, plus c'est "élite"
        0: '#e0f2fe',      # 1/16, 1/8 selon taille
        1: '#bae6fd',
        2: '#7dd3fc',
        3: '#38bdf8',
        4: '#0ea5e9',
        5: '#0284c7',
    }

    for r, paires in enumerate(bracket_state):
        for idx, p in enumerate(paires):
            cy = slot_y(r, idx)
            cx = slot_x(r)
            color = color_by_round.get(r, '#0ea5e9')
            if not p:
                # Slot vide (BYE / paire absente)
                cards_svg += f'<rect x="{cx}" y="{cy-CARD_H/2}" width="{CARD_W}" height="{CARD_H}" rx="6" fill="#f1f5f9" stroke="#cbd5e1" stroke-dasharray="3,3"/>'
                cards_svg += f'<text x="{cx+CARD_W/2}" y="{cy+5}" text-anchor="middle" font-size="11" fill="#94a3b8">— BYE —</text>'
                continue

            j = p['joueurs'][:2]
            while len(j) < 2: j.append({'nom':'?','prenom':'','classement':None})
            # Surbrillance si vainqueur final
            try:
                rg = int(p['position_raw'])
            except:
                rg = 999
            is_champ = (r == n_rounds - 1) and rg == 1
            stroke = '#f59e0b' if is_champ else color
            fill = '#fef3c7' if is_champ else '#ffffff'

            cards_svg += f'''
            <g>
              <rect x="{cx}" y="{cy-CARD_H/2}" width="{CARD_W}" height="{CARD_H}" rx="6"
                    fill="{fill}" stroke="{stroke}" stroke-width="{'2' if is_champ else '1'}"/>
              <text x="{cx+8}" y="{cy-CARD_H/2+18}" font-size="12" font-weight="600" fill="#0f172a">{(j[0]['prenom'] or '').strip()[:1] + '. ' if j[0]['prenom'] else ''}{(j[0]['nom'] or '?')[:18]}</text>
              <text x="{cx+8}" y="{cy-CARD_H/2+34}" font-size="12" font-weight="600" fill="#0f172a">{(j[1]['prenom'] or '').strip()[:1] + '. ' if j[1]['prenom'] else ''}{(j[1]['nom'] or '?')[:18]}</text>
              <text x="{cx+CARD_W-6}" y="{cy-CARD_H/2+14}" font-size="10" font-family="ui-monospace,monospace" fill="#64748b" text-anchor="end">#{p['position_raw']}</text>
              <text x="{cx+8}" y="{cy+CARD_H/2-6}" font-size="9" fill="#64748b">#{j[0]['classement'] or '?'} · #{j[1]['classement'] or '?'} · +{p['points'] or 0}pts</text>
              {'<text x="' + str(cx+CARD_W-8) + '" y="' + str(cy+CARD_H/2-6) + '" text-anchor="end" font-size="14">🏆</text>' if is_champ else ''}
            </g>'''

        # Lignes connectrices (du round r au round r+1)
        if r < n_rounds - 1:
            for i in range(0, len(paires), 2):
                if not paires[i] and (i+1 >= len(paires) or not paires[i+1]):
                    continue
                y_top = slot_y(r, i)
                y_bot = slot_y(r, i+1)
                y_next = slot_y(r+1, i//2)
                x_right = slot_x(r) + CARD_W
                x_mid = x_right + GAP_X / 2
                x_next = slot_x(r+1)
                # 2 lignes horizontales puis verticale puis horizontale
                lines_svg += f'<path d="M {x_right} {y_top} H {x_mid} V {y_next} M {x_right} {y_bot} H {x_mid}" fill="none" stroke="#cbd5e1" stroke-width="1.5"/>'
                lines_svg += f'<path d="M {x_mid} {y_next} H {x_next}" fill="none" stroke="#cbd5e1" stroke-width="1.5"/>'

    # En-têtes de colonnes
    headers_svg = ''
    round_names = []
    rs_size = bracket_size
    while rs_size >= 1:
        if rs_size == 1: round_names.append('🏆 Champion')
        elif rs_size == 2: round_names.append('Finale')
        elif rs_size == 4: round_names.append('1/2 finale')
        elif rs_size == 8: round_names.append('1/4 finale')
        elif rs_size == 16: round_names.append('1/8 finale')
        elif rs_size == 32: round_names.append('1/16 finale')
        elif rs_size == 64: round_names.append('1/32 finale')
        else: round_names.append(f'1/{rs_size} finale')
        rs_size //= 2
    for r, name in enumerate(round_names):
        x = slot_x(r) + CARD_W / 2
        headers_svg += f'<text x="{x}" y="20" text-anchor="middle" font-size="13" font-weight="700" fill="#475569">{name}</text>'

    is_inter = is_interclubs(data['nom'], data['categorie'])
    svg_total_h = total_h + PAD * 2

    html = f'''<!DOCTYPE html>
<html lang="fr"><head>
<meta charset="UTF-8"><title>Bracket — {data['nom']}</title>
<script src="https://cdn.tailwindcss.com"></script>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>* {{ font-family: 'Inter', system-ui, sans-serif; }}
  body {{ background: #f8fafc; padding: 24px; }}
  .bracket-svg {{ background: white; border-radius: 12px; border: 1px solid #e2e8f0; }}
</style></head>
<body>
<div class="max-w-[1800px] mx-auto">
  <div class="bg-gradient-to-br from-rose-500 via-pink-600 to-purple-700 rounded-2xl p-6 text-white mb-4">
    <div class="text-xs uppercase tracking-widest opacity-75 mb-1">Bracket arbre</div>
    <div class="text-3xl font-bold">{data['nom'] or 'Sans nom'}</div>
    <div class="flex flex-wrap gap-3 text-sm opacity-90 mt-2">
      <span>🏷️ {data['categorie'] or '?'}</span>
      <span>📅 {data['date'] or '?'}</span>
      <span>👥 {n_pairs} paires (bracket {bracket_size})</span>
      <span>{('🏢 Interclubs' if is_inter else '🎾 Tournoi individuel')}</span>
    </div>
  </div>
  <div class="bracket-svg overflow-x-auto p-4">
    <svg width="{total_w}" height="{svg_total_h}" xmlns="http://www.w3.org/2000/svg">
      {headers_svg}
      <g transform="translate(0, 24)">
        {lines_svg}
        {cards_svg}
      </g>
    </svg>
  </div>
  <div class="text-xs text-slate-500 mt-2 max-w-3xl">
    💡 Le bracket est reconstruit en supposant le <b>seeding standard FFT</b> (la paire de rang 1 affronte la paire de rang max, etc.).
    Les confrontations exactes ne sont pas dans la base, donc cette reconstruction reflète la structure logique du tournoi
    plutôt que les matchs réels (qui pourraient avoir eu des "upsets").
  </div>
</div>
</body></html>'''
    out_path = os.path.join(os.path.dirname(__file__), out)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"\n🌳 Bracket arbre HTML → {out_path}")
    return out_path

# ───────────────────────────────────────────────────────────────────────────
def main():
    args = sys.argv[1:]
    if not args or args[0] == 'audit':
        audit_positions()
    elif args[0] == 'top':
        top_tournois(int(args[1]) if len(args) > 1 else 15)
    elif args[0] == 'reconstruct' and len(args) > 1:
        data = reconstruct(args[1], verbose=True)
        if data:
            render_html(data)
            render_tree_html(data)
    elif args[0] == 'demo':
        # Auto : prend le 3e plus gros tournoi (les premiers sont parfois sales)
        rows = top_tournois(5)
        if rows:
            id_t = rows[2][0] if len(rows) > 2 else rows[0][0]
            print(f"\n👉 Reconstruction de '{rows[2][1] if len(rows)>2 else rows[0][1]}' (id={id_t})\n")
            data = reconstruct(id_t, verbose=True)
            if data:
                render_html(data)
                render_tree_html(data)
    else:
        print(__doc__)

if __name__ == '__main__':
    main()
