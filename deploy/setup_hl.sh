#!/bin/bash
# One-time setup for the Hyperliquid bot.
# Run after the HL wallet is funded and HL_PRIVATE_KEY is in .env
# Usage: bash /opt/cryptobot/app/deploy/setup_hl.sh

set -e
APP_DIR="/opt/cryptobot/app"
echo "=== Hyperliquid Bot Setup ==="

cd "$APP_DIR"

# Check HL_PRIVATE_KEY is set
if ! grep -q "HL_PRIVATE_KEY" .env 2>/dev/null; then
    echo "ERROR: HL_PRIVATE_KEY not found in .env"
    echo "Add it: echo 'HL_PRIVATE_KEY=0x...' >> /opt/cryptobot/app/.env"
    exit 1
fi

echo "Installing hyperliquid SDK..."
sudo -u cryptobot .venv/bin/pip install hyperliquid-python-sdk -q

echo "Installing service..."
cp "$APP_DIR/deploy/hl_cryptobot.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable hl_cryptobot

echo "Starting Hyperliquid bot..."
systemctl start hl_cryptobot

sleep 3
echo ""
echo "=== HL Bot status ==="
systemctl status hl_cryptobot --no-pager -l | head -20

echo ""
echo "Setup complete."
echo "Logs: journalctl -u hl_cryptobot -f"
echo "To stop: systemctl stop hl_cryptobot"
