# resume_monthly.ps1 — Reprend le pipeline mensuel Tenup APRES un scrape deja termine/complete.
# A utiliser quand scraper_json.py (--full puis --bilans-only) a deja tourne et que la queue
# est proche de 0 : ce script ne re-scrape RIEN, il enchaine juste les etapes derivees
# (nettoyage, partenaires, stats tournois, geo, timeline, verif) dont le dashboard a besoin.
#
# Usage interactif :  clic droit > Executer avec PowerShell
# Usage direct      :  powershell -ExecutionPolicy Bypass -File resume_monthly.ps1
#   options : -NoVacuum (saute le compactage, plus rapide)

param([switch]$NoVacuum)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
function Step($n,$t){ Write-Host "`n[$n] $t" -ForegroundColor Yellow }

Write-Host "=== Reprise pipeline mensuel Tenup (sans re-scrape) ===" -ForegroundColor Cyan

# 1) Nettoyage / compactage de la base
Step "1/8" "Nettoyage de la base..."
if ($NoVacuum) { python cleanup_db.py --apply } else { python cleanup_db.py --apply --vacuum }

# 2) Partenaires -> debloque graphe + suggesteur
Step "2/8" "Matching des partenaires..."
python match_partenaires.py --apply

# 3) Poids / force de chaque tournoi (table tournois_stats : niveau P, par equipes, nb joueurs)
Step "3/8" "tournois_stats..."
python tournois_stats.py

# 4) Difficulte des tournois (classements D'EPOQUE + pool du mois)
Step "4/8" "tournois_rating (difficulte, classements d'epoque)..."
python tournois_rating.py

# 5) Rattachement tournoi -> club organisateur (deduit du nom)
Step "5/8" "match_tournois_clubs..."
python match_tournois_clubs.py

# 6) Suppression des clubs parasites (nom = une ville)
Step "6/8" "clean_clubs..."
python clean_clubs.py --apply

# 7) Couche geo (carte) + series temporelles
Step "7/8" "build_geo + geocode_villes + build_timeline (carte + courbe)..."
python build_geo.py
python geocode_villes.py
python build_timeline.py

# 8) Verification finale
Step "8/8" "Verification des donnees..."
python validate_data.py

Write-Host "`nOK - Reprise terminee." -ForegroundColor Green
Write-Host "Etape suivante (deploiement) : uploader tenup.db sur le VPS puis 'docker compose build && docker compose up -d'."
Write-Host "  scp tenup.db UTILISATEUR@IP_VPS:/opt/padel-data/tenup.db"
Read-Host "Appuie sur Entree pour fermer"
