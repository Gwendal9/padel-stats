# scrape_prompt.ps1 — Notification + confirmation avant le refresh mensuel Tenup.
# Lance par une tache planifiee (1er mardi du mois). Affiche une fenetre de confirmation ;
# sur "Oui", enchaine automatiquement tout le pipeline (run_monthly_full.ps1 -Auto).
#
# Test manuel : clic droit > Executer avec PowerShell.

Add-Type -AssemblyName System.Windows.Forms | Out-Null

$msg = @"
Refresh mensuel Tenup padel.

Avant de lancer, verifie :
  - Firefox OUVERT et connecte a tenup.fft.fr
  - Extension 'Tab Reloader' active (~30s) sur un onglet tenup

Tout est pret ?
(Oui = lancer maintenant - Non = reporter)
"@

$r = [System.Windows.Forms.MessageBox]::Show($msg, "Scrape mensuel Tenup", 'YesNo', 'Question')

if ($r -eq 'Yes') {
    $script = Join-Path $PSScriptRoot 'run_monthly_full.ps1'
    Start-Process powershell -ArgumentList '-ExecutionPolicy','Bypass','-NoExit','-File',"`"$script`"",'-Auto'
} else {
    [System.Windows.Forms.MessageBox]::Show(
        "Scrape reporte. Tu peux le relancer quand tu veux (clic droit > Executer sur run_monthly_full.ps1).",
        "Tenup", 'OK', 'Information') | Out-Null
}
