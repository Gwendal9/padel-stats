#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
# setup_vps.sh — Installation complète du scraper tenup sur VPS
# ═══════════════════════════════════════════════════════════════════
# Usage : bash setup_vps.sh
# Testé sur Ubuntu 22.04 LTS
# ═══════════════════════════════════════════════════════════════════

set -e
REPO_DIR="${1:-/opt/tenup_scraper}"

echo "════════════════════════════════════════"
echo " Setup VPS — Tenup Padel Scraper"
echo " Dossier cible : $REPO_DIR"
echo "════════════════════════════════════════"
echo ""

# ── 1. Dépendances système ──────────────────────────────────────
echo "[1/6] Mise à jour système + dépendances..."
apt-get update -qq
apt-get install -y -qq \
    python3 python3-pip python3-venv \
    git curl wget \
    xvfb \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 \
    libxfixes3 libxrandr2 libgbm1 libasound2t64 \
    libpangocairo-1.0-0 libcairo-gobject2 libgtk-3-0 \
    fonts-liberation libappindicator3-1 xdg-utils
echo "   ✅ Dépendances système installées"

# ── 2. Répertoire du projet ─────────────────────────────────────
echo ""
echo "[2/6] Création du dossier projet..."
mkdir -p "$REPO_DIR"
echo "   ✅ $REPO_DIR prêt"
echo "   → Copie ton projet dans $REPO_DIR/backend/ et $REPO_DIR/frontend/"
echo "   → (rsync -av ./tenup_scraper_v2/ user@vps:$REPO_DIR/)"

# ── 3. Dépendances Python ───────────────────────────────────────
echo ""
echo "[3/6] Installation des packages Python..."
pip3 install --break-system-packages \
    curl_cffi \
    httpx \
    playwright \
    beautifulsoup4 \
    requests \
    flask \
    flask-cors \
    gunicorn \
    lxml
echo "   ✅ Packages Python installés"

# ── 4. Playwright + Chromium ────────────────────────────────────
echo ""
echo "[4/6] Installation Chromium pour Playwright..."
python3 -m playwright install chromium
python3 -m playwright install-deps chromium
echo "   ✅ Chromium Playwright installé"

# ── 5. Test Xvfb ───────────────────────────────────────────────
echo ""
echo "[5/6] Test Xvfb (affichage virtuel)..."
Xvfb :99 -screen 0 1280x800x24 &
XVFB_PID=$!
sleep 2

if kill -0 $XVFB_PID 2>/dev/null; then
    echo "   ✅ Xvfb démarre correctement (PID $XVFB_PID)"
    kill $XVFB_PID
else
    echo "   ❌ Xvfb ne démarre pas — vérifier les dépendances"
    exit 1
fi

# ── 6. Créer le service systemd (optionnel, pour le backend Flask) ─
echo ""
echo "[6/6] Création service systemd pour le backend Flask..."

cat > /etc/systemd/system/tenup-backend.service << EOF
[Unit]
Description=Tenup Padel Backend (Flask)
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$REPO_DIR/backend
ExecStart=/usr/bin/python3 -m gunicorn --bind 0.0.0.0:5001 --workers 2 app:app
Restart=always
RestartSec=5
Environment=DATABASE_URL=sqlite:///$REPO_DIR/backend/tenup.db

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable tenup-backend.service
echo "   ✅ Service systemd créé (tenup-backend)"
echo "   → Démarre avec : systemctl start tenup-backend"

echo ""
echo "════════════════════════════════════════"
echo " ✅ Setup terminé !"
echo "════════════════════════════════════════"
echo ""
echo "Étapes suivantes :"
echo "  1. Copier le projet : rsync -av . user@vps:$REPO_DIR/"
echo "  2. Copier la DB    : scp backend/tenup.db user@vps:$REPO_DIR/backend/"
echo "  3. Copier cookies  : scp backend/cookies.json user@vps:$REPO_DIR/backend/"
echo "  4. Tester cookies  : cd $REPO_DIR/backend && bash ../test_xvfb_cookies.sh"
echo "  5. Démarrer backend: systemctl start tenup-backend"
echo ""
