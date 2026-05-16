"""
download_classement_csv.py — Télécharge le classement FFT padel depuis l'API officielle.

API : POST https://tenup.fft.fr/back/public/v2/classements/recherche
      (API publique, ne nécessite pas de login — seulement un cookie datadome)

Exporte :
  joueurs_padel_H.csv   — tous les joueurs hommes classés
  joueurs_padel_F.csv   — tous les joueuses femmes classées

Usage :
    python download_classement_csv.py             # télécharge H + F
    python download_classement_csv.py --sexe H    # seulement les hommes
    python download_classement_csv.py --sexe F    # seulement les femmes
    python download_classement_csv.py --taille-page 200  # pages plus grandes

Après téléchargement, lancer :
    python import_csv_classements.py joueurs_padel_H.csv joueurs_padel_F.csv
"""
import math
import json
import os
import sys
import time
import argparse
import requests
import pandas as pd
from tqdm import tqdm
from datetime import datetime

BASE_DIR   = os.path.dirname(__file__)
COOKIES_FILE = os.path.join(BASE_DIR, 'cookies.json')
URL        = "https://tenup.fft.fr/back/public/v2/classements/recherche"

HEADERS = {
    "Accept":          "application/json",
    "Content-Type":    "application/json",
    "Origin":          "https://tenup.fft.fr",
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
}


def load_cookies() -> dict:
    """
    Charge les cookies depuis cookies.json.
    Seul 'datadome' est strictement nécessaire pour cette API publique.
    """
    if not os.path.exists(COOKIES_FILE):
        return {}
    with open(COOKIES_FILE) as f:
        raw = json.load(f)
    # Format liste [{"name":..., "value":...}] ou dict
    if isinstance(raw, list):
        return {c['name']: c['value'] for c in raw if c.get('value')}
    return raw


def download_sexe(sexe: str, taille_page: int = 100) -> pd.DataFrame:
    """Télécharge tous les joueurs d'un sexe donné ('H' ou 'F')."""
    assert sexe in ('H', 'F')
    label = "Hommes" if sexe == "H" else "Femmes"

    cookies = load_cookies()
    if not cookies:
        print(f"  ⚠️  cookies.json absent — l'API peut rejeter la requête (erreur 403/captcha)")
        print(f"     → Renouvelle tes cookies depuis le navigateur si ça plante")

    session = requests.Session()

    # Referer différent selon le sexe
    headers = dict(HEADERS)
    headers['Referer'] = f"https://tenup.fft.fr/classement-padel?categorie={sexe}"

    payload = {
        "categorie":  sexe,
        "sexe":       sexe,
        "pratique":   "PADEL",
        "page":       1,
        "taillePage": taille_page,
    }

    print(f"\n{'━'*50}")
    print(f"📥 Téléchargement classement {label}...")

    # ── Page 1 ──────────────────────────────────────────────────────
    try:
        r = session.post(URL, headers=headers, cookies=cookies, json=payload, timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  ❌ Erreur sur la 1ère requête : {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"     Status: {e.response.status_code}")
            print(f"     Body:   {e.response.text[:500]}")
        return pd.DataFrame()

    total    = data.get("total", 0)
    nb_pages = math.ceil(total / taille_page)
    print(f"  Total joueurs : {total:,}")
    print(f"  Pages         : {nb_pages:,}  ({taille_page} joueurs/page)")

    all_dfs = []
    joueurs = data.get("joueurs", [])
    if joueurs:
        df1 = pd.json_normalize(joueurs)
        df1["page_source"] = 1
        all_dfs.append(df1)

    # ── Pages suivantes ─────────────────────────────────────────────
    errors = 0
    for page in tqdm(range(2, nb_pages + 1), desc=f"  {label}"):
        payload["page"] = page
        try:
            r = session.post(URL, headers=headers, cookies=cookies, json=payload, timeout=30)
            r.raise_for_status()
            data = r.json()
            joueurs = data.get("joueurs", [])
            if joueurs:
                df = pd.json_normalize(joueurs)
                df["page_source"] = page
                all_dfs.append(df)
        except Exception as e:
            errors += 1
            print(f"\n  ⚠️  Erreur page {page}: {e}")
            if errors >= 5:
                print("  ❌ Trop d'erreurs consécutives — arrêt")
                break
            time.sleep(2)

    if not all_dfs:
        print(f"  ❌ Aucune donnée récupérée pour {label}")
        return pd.DataFrame()

    # ── Concaténation + dédoublonnage ────────────────────────────────
    df = pd.concat(all_dfs, ignore_index=True)
    before = len(df)
    if "idCrm" in df.columns:
        df = df.drop_duplicates(subset=["idCrm"], keep="first")
    after = len(df)

    if before != after:
        print(f"\n  ℹ️  {before - after} doublons supprimés")

    print(f"  ✅ {after:,} joueurs {label} récupérés")
    return df


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sexe', choices=['H', 'F', 'HF'], default='HF',
                        help="Sexe à télécharger (défaut: HF = les deux)")
    parser.add_argument('--taille-page', type=int, default=100,
                        help="Joueurs par page (défaut: 100)")
    args = parser.parse_args()

    mois = datetime.now().strftime('%Y-%m')
    print(f"=== Download classement FFT Padel — {mois} ===")

    sexes = []
    if 'H' in args.sexe:
        sexes.append('H')
    if 'F' in args.sexe:
        sexes.append('F')

    exported = []
    for sexe in sexes:
        df = download_sexe(sexe, taille_page=args.taille_page)
        if df.empty:
            print(f"⚠️  Pas de données pour {sexe} — fichier non exporté")
            continue

        # Ajouter colonne sexe pour faciliter l'import
        df['sexe'] = sexe

        filename = f"joueurs_padel_{sexe}.csv"
        out_path = os.path.join(BASE_DIR, filename)
        df.to_csv(out_path, index=False, encoding='utf-8-sig')
        print(f"  💾 Exporté : {filename}  ({len(df):,} lignes)")
        exported.append(filename)

    print(f"\n{'━'*50}")
    print(f"✅ Fichiers exportés : {', '.join(exported)}")
    print()
    print("Prochaines étapes :")
    print(f"  python import_csv_classements.py {' '.join(exported)}")
    print(f"    → Classements + variations mis à jour en DB en quelques secondes")
    print()
    print("  Puis pour les participations (tournois/partenaires) :")
    print("  python monthly_refresh.py")
    print("  python scraper_http.py --workers 15")


if __name__ == '__main__':
    main()
