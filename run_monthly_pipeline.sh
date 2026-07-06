#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
# run_monthly_pipeline.sh — Pipeline mensuel complet
# ═══════════════════════════════════════════════════════════════════
# Déclenché par n8n le 1er mardi de chaque mois.
# Enchaîne : cookies refresh (Xvfb) → monthly_refresh → scraper
#
# Usage :
#   bash run_monthly_pipeline.sh             # mode normal
#   bash run_monthly_pipeline.sh --force     # force même si déjà fait ce mois
#   bash run_monthly_pipeline.sh --test      # test cookies uniquement
#
# Logs : /var/log/tenup/pipeline_YYYY-MM.log
# ═══════════════════════════════════════════════════════════════════

set -euo pipefail

# ── Config (adapter si besoin) ──────────────────────────────────
BACKEND_DIR="${BACKEND_DIR:-/opt/tenup_scraper/backend}"
LOG_DIR="/var/log/tenup"
DISPLAY_NUM=":99"
WORKERS="${WORKERS:-15}"
NOTIFY_WEBHOOK="${NOTIFY_WEBHOOK:-}"   # URL webhook n8n ou Telegram (optionnel)
FORCE="${1:-}"
TEST_ONLY=false
[[ "$FORCE" == "--test" ]] && TEST_ONLY=true

MOIS=$(date +%Y-%m)
LOG_FILE="$LOG_DIR/pipeline_${MOIS}.log"
PID_FILE="/tmp/tenup_scraper.pid"

# ── Initialisation ──────────────────────────────────────────────
mkdir -p "$LOG_DIR"

# Tout logguer ET afficher dans le terminal (pour n8n)
exec > >(tee -a "$LOG_FILE") 2>&1

echo ""
echo "════════════════════════════════════════════════"
echo " Tenup Monthly Pipeline — $MOIS"
echo " $(date '+%Y-%m-%d %H:%M:%S')"
echo "════════════════════════════════════════════════"

# ── Vérifier qu'un scrape n'est pas déjà en cours ──────────────
if [[ -f "$PID_FILE" ]]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "⚠️  Un scraper est déjà en cours (PID $OLD_PID) — abandon."
        exit 1
    else
        echo "ℹ️  PID file obsolète supprimé."
        rm -f "$PID_FILE"
    fi
fi

# ── Fonction : envoyer notification ────────────────────────────
notify() {
    local msg="$1"
    echo "[NOTIF] $msg"
    if [[ -n "$NOTIFY_WEBHOOK" ]]; then
        curl -s -X POST "$NOTIFY_WEBHOOK" \
            -H "Content-Type: application/json" \
            -d "{\"text\": \"🎾 Tenup Scraper\\n$msg\"}" \
            || true
    fi
}

# ── Étape 1 : Démarrer Xvfb ────────────────────────────────────
echo ""
echo "[1/4] Démarrage Xvfb (affichage virtuel)..."

# Tuer un éventuel Xvfb zombie sur ce display
pkill -f "Xvfb $DISPLAY_NUM" 2>/dev/null || true
sleep 1

Xvfb "$DISPLAY_NUM" -screen 0 1280x800x24 -ac +extension GLX +render -noreset &
XVFB_PID=$!
sleep 2

if ! kill -0 "$XVFB_PID" 2>/dev/null; then
    echo "❌ Xvfb n'a pas démarré"
    notify "❌ Échec Xvfb — pipeline annulé ($MOIS)"
    exit 1
fi
echo "   ✅ Xvfb actif (PID $XVFB_PID, display $DISPLAY_NUM)"
export DISPLAY="$DISPLAY_NUM"

# Cleanup Xvfb à la sortie du script
trap "echo ''; echo 'Nettoyage...'; kill $XVFB_PID 2>/dev/null || true; rm -f $PID_FILE" EXIT

# ── Étape 2 : Refresh cookies via Playwright ───────────────────
echo ""
echo "[2/4] Refresh cookies TenUp (Playwright + Xvfb)..."
cd "$BACKEND_DIR"

# Tentatives : 3 essais max
COOKIE_OK=false
for attempt in 1 2 3; do
    echo "   Tentative $attempt/3..."
    if python3 auto_refresh_cookies.py --visible --timeout 120; then
        COOKIE_OK=true
        echo "   ✅ Cookies rafraîchis avec succès !"
        break
    else
        echo "   ⚠️  Tentative $attempt échouée"
        sleep 15
    fi
done

if [[ "$COOKIE_OK" == "false" ]]; then
    echo "❌ Impossible de rafraîchir les cookies après 3 essais"
    notify "❌ Échec refresh cookies — pipeline annulé ($MOIS). Vérifie manuellement."
    exit 1
fi

if [[ "$TEST_ONLY" == "true" ]]; then
    echo ""
    echo "✅ --test : cookies OK, pipeline arrêté ici."
    exit 0
fi

# ── Étape 3 : Monthly refresh (reset queue) ────────────────────
echo ""
echo "[3/4] Monthly refresh (remise en queue joueurs actifs)..."

FORCE_FLAG=""
[[ "$FORCE" == "--force" ]] && FORCE_FLAG="--force"

python3 monthly_refresh.py --smart $FORCE_FLAG
echo "   ✅ Queue prête"

# ── Étape 4 : Lancer le scraper en background ──────────────────
echo ""
echo "[4/4] Lancement scraper ($WORKERS workers)..."
notify "🚀 Scrape mensuel démarré ($MOIS) — ~3-5h estimées"

# Lancer en background, capturer le PID
nohup python3 scraper_http.py --workers "$WORKERS" \
    >> "$LOG_FILE" 2>&1 &
SCRAPER_PID=$!
echo "$SCRAPER_PID" > "$PID_FILE"
echo "   ✅ Scraper lancé (PID $SCRAPER_PID)"
echo "   📄 Logs : $LOG_FILE"
echo "   📊 Avancement : python3 check_db.py"

# Attendre la fin du scraper (ou timeout 8h)
echo ""
echo "   ⏳ En attente de fin du scrape (max 8h)..."
TIMEOUT_SECS=$((8 * 3600))
ELAPSED=0
INTERVAL=60   # vérif toutes les 60s

while kill -0 "$SCRAPER_PID" 2>/dev/null; do
    sleep "$INTERVAL"
    ELAPSED=$((ELAPSED + INTERVAL))

    if (( ELAPSED % 1800 == 0 )); then
        # Log de progression toutes les 30 min
        PENDING=$(python3 -c "
import sqlite3, os
db = os.path.join('$BACKEND_DIR', 'tenup.db')
conn = sqlite3.connect(db)
p = conn.execute(\"SELECT COUNT(*) FROM scrape_queue WHERE statut='pending'\").fetchone()[0]
d = conn.execute(\"SELECT COUNT(*) FROM scrape_queue WHERE statut='done'\").fetchone()[0]
print(f'pending={p}, done={d}')
conn.close()
" 2>/dev/null || echo "?")
        echo "   [$(date '+%H:%M')] $PENDING"
    fi

    if (( ELAPSED >= TIMEOUT_SECS )); then
        echo "   ⚠️  Timeout 8h atteint — scraper toujours en cours"
        notify "⚠️  Scrape $MOIS toujours en cours après 8h (PID $SCRAPER_PID)"
        # On ne tue pas le scraper, on laisse tourner
        rm -f "$PID_FILE"
        exit 0
    fi
done

rm -f "$PID_FILE"

# ── Bilan final ─────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════"
TOTAL=$(python3 -c "
import sqlite3, os
db = os.path.join('$BACKEND_DIR', 'tenup.db')
conn = sqlite3.connect(db)
t = conn.execute(\"SELECT COUNT(*) FROM joueurs WHERE scraped_at IS NOT NULL\").fetchone()[0]
cl = conn.execute(\"SELECT COUNT(*) FROM joueurs WHERE classement IS NOT NULL\").fetchone()[0]
conn.close()
print(f'{t} joueurs scrapés, {cl} classés')
" 2>/dev/null || echo "stats indisponibles")

echo " ✅ Scrape $MOIS terminé !"
echo " $TOTAL"
echo " Durée : $((ELAPSED / 60)) minutes"
echo "════════════════════════════════════════════════"

notify "✅ Scrape $MOIS terminé ! $TOTAL — Durée : $((ELAPSED / 60)) min"
