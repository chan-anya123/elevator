#!/bin/bash
set -e

BOARD_IP=$1

if [ -z "$BOARD_IP" ]; then
  echo "❌ Usage: ./deploy.sh <BOARD_IP>"
  exit 1
fi

echo "======================================"
echo "🚀 DEPLOY TO $BOARD_IP"
echo "======================================"

# ======================================
# COPY ONLY RUNTIME FILES
# ======================================

echo "📥 Upload main.py ..."
scp /home/cookies/lift/lift_real/lift/main.py \
arduino@$BOARD_IP:/home/arduino/lift/

echo "📥 Upload templates ..."
scp -r /home/cookies/lift/lift_real/lift/templates \
arduino@$BOARD_IP:/home/arduino/lift/

echo "📥 Upload requirements ..."
scp /home/cookies/lift/lift_real/lift/requirements.txt \
arduino@$BOARD_IP:/home/arduino/lift/

# ======================================
# REMOTE INSTALL + RESTART
# ======================================

ssh arduino@$BOARD_IP << 'EOF'

set -e

cd /home/arduino/lift

echo "🐍 Activate venv..."
python3 -m venv venv || true
source venv/bin/activate

echo "📦 Install packages..."
pip install -r requirements.txt

echo "🔄 Restart service..."
sudo systemctl restart lift-service.service

echo "✅ Service restarted"

EOF

echo "======================================"
echo "✅ DONE $BOARD_IP"
echo "======================================"