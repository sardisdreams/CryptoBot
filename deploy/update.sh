#!/bin/bash
# Run this on the server to pull latest code and restart services
# Usage: bash /opt/cryptobot/app/deploy/update.sh
set -e

APP_DIR="/opt/cryptobot/app"
echo "=== CryptoBot Update ==="

cd "$APP_DIR"

# Pull latest code
echo "Pulling latest code..."
sudo -u cryptobot git pull

# Install any new dependencies
echo "Updating dependencies..."
sudo -u cryptobot .venv/bin/pip install -r requirements.txt -q

# Restart services
echo "Restarting services..."
systemctl restart cryptobot
systemctl restart cryptobot-dashboard

# Wait and check status
sleep 3
echo ""
echo "=== Bot status ==="
systemctl status cryptobot --no-pager -l | head -20

echo ""
echo "=== Dashboard status ==="
systemctl status cryptobot-dashboard --no-pager -l | head -10

echo ""
echo "Update complete. Logs: journalctl -u cryptobot -f"
