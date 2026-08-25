# run_monthly.ps1 — Refresh mensuel Tenup padel (à lancer le 1er mardi du mois)
# Clic droit > Exécuter avec PowerShell, ou : powershell -ExecutionPolicy Bypass -File run_monthly.ps1
#
# PRÉREQUIS (sinon ça pausera sur des 401) :
#   - Firefox OUVERT, connecté à tenup.fft.fr
#   - Extension "Tab Reloader" active (~30s) sur un onglet tenup (ex. /fichejoueur/1953828852)
#
# Mode par défaut : --full (re-scrape TOUS les bilans, ~3h en autonome).
#   Raison : le rang d'un joueur bouge meme sans qu'il joue (resultats des autres),
#   donc on ne peut pas se fier a "evolution != 0" pour savoir qui a joue -> on prend tout.
#   La phase liste met de toute facon a jour le classement de TOUS les joueurs (H+F) +
#   capture les nouveaux joueurs, en full comme en smart.
# Alternative rapide (~30-60 min) mais qui peut rater un joueur ayant joue sans bouger
#   au classement : remplace --full par --smart ci-dessous.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "=== Refresh mensuel Tenup padel ===" -ForegroundColor Cyan
Write-Host "Vérifie : Firefox connecte a tenup + Tab Reloader actif (~30s) sur un onglet tenup."
Read-Host "Quand c'est pret, appuie sur Entree pour demarrer"

# 1) Sauvegarde
$bak = "tenup_backup_$(Get-Date -Format yyyyMMdd_HHmmss).db"
Write-Host "`n[1/7] Sauvegarde -> $bak" -ForegroundColor Yellow
Copy-Item tenup.db $bak

# 2) Scrape : liste classement (H+F, met a jour TOUS les rangs + nouveaux joueurs) + tous les bilans
Write-Host "`n[2/7] Scrape complet (liste + bilans)..." -ForegroundColor Yellow
python scraper_json.py --full --workers 8 --cookie-source firefox

# 3) Reprise auto des eventuels restants (si la session a saute)
Write-Host "`n[3/7] Reprise des bilans restants..." -ForegroundColor Yellow
python scraper_json.py --bilans-only --workers 8 --cookie-source firefox

# 4) Nettoyage (orphelines, vieilles participations, "None None")
Write-Host "`n[4/7] Nettoyage de la base..." -ForegroundColor Yellow
python cleanup_db.py --apply

# 5) Reconstruction de la couche donnees derivee (un script a la fois)
Write-Host "`n[5/7] Reconstruction couche donnees (partenaires / tournois / geo)..." -ForegroundColor Yellow
python match_partenaires.py --apply
python tournois_stats.py
python build_geo.py
python geocode_villes.py
python build_timeline.py     # timeline.json (courbes + delta mensuel H/F "vs mois precedent")

# 6) Verif finale
Write-Host "`n[6/7] Verification..." -ForegroundColor Green
python validate_data.py

# 7) Regeneration des donnees du dashboard (stats globales JSON + cache precalcule)
Write-Host "`n[7/7] Regeneration du dashboard (stats globales + cache)..." -ForegroundColor Yellow
Push-Location ..\frontend\dashboard
python data_builder.py
python precompute.py
Pop-Location

Write-Host "`nTermine. Si 'queue_restante' n'est pas ~0, relance :" -ForegroundColor Cyan
Write-Host "   python scraper_json.py --bilans-only --workers 8 --cookie-source firefox"
Read-Host "Appuie sur Entree pour fermer"
