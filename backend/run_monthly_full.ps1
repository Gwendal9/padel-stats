# run_monthly_full.ps1 — Refresh mensuel COMPLET Tenup padel
# = scrape FFT + TOUTES les etapes derivees dont le dashboard a besoin.
# A lancer le 1er mardi du mois (apres publication du nouveau classement FFT).
#
# PREREQUIS (sinon ca pausera sur des 401) :
#   - Firefox OUVERT, connecte a tenup.fft.fr
#   - Extension "Tab Reloader" active (~30s) sur un onglet tenup (ex. /fichejoueur/1953828852)
#
# Usage interactif :  clic droit > Executer avec PowerShell
# Usage auto (tache planifiee) :  powershell -ExecutionPolicy Bypass -File run_monthly_full.ps1 -Auto
#   options : -Smart (scrape incremental, ~30-60 min)   -NoVacuum (saute le compactage)

param([switch]$Auto, [switch]$Smart, [switch]$NoVacuum)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
function Step($n,$t){ Write-Host "`n[$n] $t" -ForegroundColor Yellow }

Write-Host "=== Refresh mensuel COMPLET Tenup padel ===" -ForegroundColor Cyan
if (-not $Auto) {
  Write-Host "Verifie : Firefox connecte a tenup + Tab Reloader actif (~30s) sur un onglet tenup."
  Read-Host "Quand c'est pret, appuie sur Entree pour demarrer"
}

# 1) Sauvegarde
$bak = "tenup_backup_$(Get-Date -Format yyyyMMdd_HHmmss).db"
Step "1/11" "Sauvegarde -> $bak"
Copy-Item tenup.db $bak

# 2) Scrape : liste classement (H+F, met a jour TOUS les rangs + nouveaux joueurs) + bilans
$mode = if ($Smart) { "--smart" } else { "--full" }
Step "2/11" "Scrape $mode (liste + bilans)..."
python scraper_json.py $mode --workers 8 --cookie-source firefox

# 3) Reprise auto des bilans restants (si la session a saute)
Step "3/11" "Reprise des bilans restants..."
python scraper_json.py --bilans-only --workers 8 --cookie-source firefox

# 4) Nettoyage / compactage de la base
Step "4/11" "Nettoyage de la base..."
if ($NoVacuum) { python cleanup_db.py --apply } else { python cleanup_db.py --apply --vacuum }

# 5) Partenaires -> debloque graphe + suggesteur
Step "5/11" "Matching des partenaires..."
python match_partenaires.py --apply

# 6) Poids / force de chaque tournoi (table tournois_stats : niveau P, par equipes, nb joueurs)
Step "6/11" "tournois_stats..."
python tournois_stats.py

# 7) Difficulte des tournois (classements D'EPOQUE + pool du mois)
Step "7/11" "tournois_rating (difficulte, classements d'epoque)..."
python tournois_rating.py

# 8) Rattachement tournoi -> club organisateur (deduit du nom)
Step "8/11" "match_tournois_clubs..."
python match_tournois_clubs.py

# 9) Suppression des clubs parasites (nom = une ville)
Step "9/11" "clean_clubs..."
python clean_clubs.py --apply

# 10) Couche geo (carte) : stats_geo_* + coordonnees des villes/clubs
Step "10/11" "build_geo + geocode_villes (carte)..."
python build_geo.py
python geocode_villes.py

# 11) Verification finale
Step "11/11" "Verification des donnees..."
python validate_data.py

Write-Host "`nOK - Refresh mensuel COMPLET termine." -ForegroundColor Green
Write-Host "Etape suivante (deploiement) : uploader tenup.db sur le VPS puis 'docker compose build && docker compose up -d'."
Write-Host "  scp tenup.db UTILISATEUR@IP_VPS:/opt/padel-data/tenup.db"
if (-not $Auto) { Read-Host "Appuie sur Entree pour fermer" }
