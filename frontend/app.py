from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
import json
import re
import os
from playwright.sync_api import sync_playwright

app = Flask(__name__, static_folder='.')
CORS(app)

SESSION = requests.Session()
SESSION.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept-Language': 'fr-FR,fr;q=0.9',
    'Referer': 'https://tenup.fft.fr/',
})

TENUP_BASE   = 'https://tenup.fft.fr'
SEARCH_PAGE  = f'{TENUP_BASE}/trouver/une-competition'
AJAX_URL     = f'{TENUP_BASE}/system/ajax'
COOKIES_FILE = os.path.join(os.path.dirname(__file__), 'cookies.json')
RAW_COOKIES  = {}

def load_cookies():
    global RAW_COOKIES
    if not os.path.exists(COOKIES_FILE):
        print("⚠️  cookies.json introuvable")
        return
    with open(COOKIES_FILE) as f:
        data = json.load(f)
    RAW_COOKIES = data
    for name, value in data.items():
        if value and not value.startswith('COLLE_'):
            SESSION.cookies.set(name, value, domain='tenup.fft.fr')
    print("✅ Cookies chargés")

load_cookies()

def get_form_tokens():
    resp = SESSION.get(SEARCH_PAGE)
    soup = BeautifulSoup(resp.text, 'html.parser')
    form_build_id = ''
    form_token = ''
    bi = soup.find('input', {'name': 'form_build_id'})
    if bi: form_build_id = bi.get('value', '')
    ft = soup.find('input', {'name': 'form_token'})
    if ft: form_token = ft.get('value', '')
    ajax_html_ids = []
    m = re.search(r'"ajax_html_ids":\s*\[([^\]]+)\]', resp.text)
    if m:
        ajax_html_ids = [s.strip().strip('"') for s in m.group(1).split(',')]
    return form_build_id, form_token, 'recherche_tournois_form', ajax_html_ids

def parse_tournaments(ajax_response):
    tournaments = []
    for command in ajax_response:
        if command.get('command') == 'insert' and 'recherche_tournois' in command.get('selector', ''):
            html = command.get('data', '')
            soup = BeautifulSoup(html, 'html.parser')
            items = soup.select('article, .views-row, [class*="tournoi"], [class*="competition"]')
            for item in items:
                t = {}
                name_el = item.select_one('h2, h3, .field--name-title, [class*="title"]')
                if name_el: t['nom'] = name_el.get_text(strip=True)
                cat_match = re.search(r'\b(P\d+|DM|DS|MD|DD|PM)\b', t.get('nom', ''))
                if cat_match: t['categorie'] = cat_match.group(1)
                date_el = item.select_one('time, [class*="date"]')
                if date_el: t['date'] = date_el.get_text(strip=True)
                club_el = item.select_one('[class*="club"]')
                if club_el: t['club'] = club_el.get_text(strip=True)
                dist_el = item.select_one('[class*="distance"]')
                if dist_el: t['distance'] = dist_el.get_text(strip=True)
                link_el = item.select_one('a[href]')
                if link_el:
                    href = link_el.get('href', '')
                    t['lien'] = f"{TENUP_BASE}{href}" if href.startswith('/') else href
                if t.get('nom'):
                    tournaments.append(t)
            break
    return tournaments

def fetch_profile_with_playwright(licence_id, pratique):
    url = f"{TENUP_BASE}/classement/{licence_id}/{pratique}"
    pw_cookies = []
    for name, value in RAW_COOKIES.items():
        if value and not value.startswith('COLLE_'):
            pw_cookies.append({'name': name, 'value': value, 'domain': 'tenup.fft.fr', 'path': '/'})
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            locale='fr-FR',
        )
        if pw_cookies:
            context.add_cookies(pw_cookies)
        page = context.new_page()
        page.goto(url, wait_until='networkidle', timeout=30000)
        try:
            page.wait_for_selector('table, .palmares, [class*="classement"]', timeout=10000)
        except:
            pass
        html = page.content()
        browser.close()
    return html

def parse_profile(html, licence_id, pratique):
    soup = BeautifulSoup(html, 'html.parser')
    profile = {
        'licence_id': licence_id,
        'pratique': pratique,
        'nom': '', 'prenom': '',
        'classement': '', 'points_total': 0,
        'ville': '', 'sexe': '', 'naissance': '',
        'tournaments': []
    }

    # Extraction depuis fft_fiche_joueur — parser d'accolades propre
    idx = html.find('"fft_fiche_joueur"')
    if idx != -1:
        start = html.find('{', idx)
        depth = 0
        end = start
        for i, c in enumerate(html[start:], start):
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        try:
            fiche = json.loads(html[start:end])
            profile['nom']       = fiche.get('nom', '')
            profile['prenom']    = fiche.get('prenom', '')
            profile['ville']     = fiche.get('ville', '')
            profile['sexe']      = fiche.get('sexe', '')
            profile['naissance'] = fiche.get('birthYear', '')
            if fiche.get('echelon'):
                profile['classement'] = str(fiche['echelon'])
        except Exception as e:
            print(f"⚠️ fft_fiche_joueur parse error: {e}")

    # Fallback nom depuis h1
    if not profile['nom']:
        name_el = soup.select_one('h1, [class*="joueur-nom"], [class*="player-name"]')
        if name_el:
            full = name_el.get_text(strip=True)
            parts = full.split()
            profile['nom'] = parts[0] if parts else ''
            profile['prenom'] = ' '.join(parts[1:]) if len(parts) > 1 else ''

    # Points total
    pts_text = soup.find(string=re.compile(r'points?\s+comptabilis', re.I))
    if pts_text:
        m2 = re.search(r'(\d[\d\s]*)\s+points', str(pts_text.parent.parent), re.I)
        if m2:
            profile['points_total'] = int(m2.group(1).replace(' ', ''))

    # Tableau des tournois
    tables = soup.select('table')
    for table in tables:
        rows = table.select('tbody tr')
        for row in rows:
            cells = row.select('td')
            if len(cells) < 5:
                continue
            texts = [c.get_text(strip=True) for c in cells]
            if not re.match(r'\d{2}/\d{2}/\d{4}', texts[0]):
                continue
            t = {
                'date':       texts[0],
                'nom':        texts[1] if len(texts) > 1 else '',
                'categorie':  texts[2] if len(texts) > 2 else '',
                'type':       texts[3] if len(texts) > 3 else '',
                'partenaire': texts[4] if len(texts) > 4 else '',
                'position':   texts[5] if len(texts) > 5 else '',
                'points':     texts[6] if len(texts) > 6 else '',
                'expiration': texts[7] if len(texts) > 7 else '',
            }
            link_el = cells[1].select_one('a[href]') if len(cells) > 1 else None
            if link_el:
                href = link_el.get('href', '')
                t['lien'] = f"{TENUP_BASE}{href}" if href.startswith('/') else href
            profile['tournaments'].append(t)

    return profile

# ═══ Routes ═══════════════════════════════════════════════════════

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/api/cookies_status')
def cookies_status():
    has = 'SHARED_SESSION_JAVA' in SESSION.cookies
    return jsonify({'authenticated': has})

@app.route('/api/search', methods=['POST'])
def search():
    params = request.json
    try:
        form_build_id, form_token, form_id, ajax_html_ids = get_form_tokens()
        form_data = [
            ('recherche_type', 'ville'),
            ('ville[autocomplete][country]', 'fr'),
            ('ville[autocomplete][textfield]', params.get('ville', '')),
            ('ville[autocomplete][value_container][value_field]', params.get('ville', '')),
            ('ville[autocomplete][value_container][label_field]', params.get('ville', '')),
            ('ville[autocomplete][value_container][lat_field]', params.get('lat', '')),
            ('ville[autocomplete][value_container][lng_field]', params.get('lng', '')),
            ('ville[distance][value_field]', params.get('distance', '30')),
            ('club[autocomplete][textfield]', ''),
            ('club[autocomplete][value_container][value_field]', ''),
            ('club[autocomplete][value_container][label_field]', ''),
            ('filter_mine', '0'),
            ('pratique', params.get('pratique', 'PADEL')),
            ('date[start]', params.get('date_start', '')),
            ('date[end]', params.get('date_end', '')),
            ('page', str(params.get('page', 0))),
            ('sort', '_DIST_'),
            ('form_build_id', form_build_id),
            ('form_token', form_token),
            ('form_id', form_id),
            ('_triggering_element_name', 'submit_main'),
            ('_triggering_element_value', 'Rechercher'),
        ]
        for aid in ajax_html_ids:
            form_data.append(('ajax_html_ids[]', aid))
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'X-Requested-With': 'XMLHttpRequest',
            'Accept': 'application/json, text/javascript, */*; q=0.01',
        }
        resp = SESSION.post(AJAX_URL, data=form_data, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        tournaments = parse_tournaments(data)
        total = 0
        for cmd in data:
            m = re.search(r'(\d+)\s+[Rr][ée]sultat', str(cmd.get('data', '')))
            if m:
                total = int(m.group(1))
                break
        return jsonify({'success': True, 'tournaments': tournaments, 'total': total})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/profile/<licence_id>')
def get_profile(licence_id):
    pratique = request.args.get('pratique', 'padel')
    try:
        print(f"🎭 Playwright → {licence_id}/{pratique}")
        html = fetch_profile_with_playwright(licence_id, pratique)
        profile = parse_profile(html, licence_id, pratique)
        print(f"✅ {len(profile['tournaments'])} tournois trouvés")
        return jsonify({'success': True, 'profile': profile})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/debug/<licence_id>')
def debug_profile(licence_id):
    pratique = request.args.get('pratique', 'padel')
    try:
        html = fetch_profile_with_playwright(licence_id, pratique)
        return html, 200, {'Content-Type': 'text/html'}
    except Exception as e:
        return str(e), 500

if __name__ == '__main__':
    print("🎾 Tenup Explorer v2 — http://localhost:5000")
    app.run(debug=True, port=5000)
