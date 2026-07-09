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

# Static analysis — catch undefined names and unused imports before restart
echo "Running lint check..."
sudo -u cryptobot .venv/bin/ruff check bot/ main.py dashboard.py
if [ $? -ne 0 ]; then
    echo "DEPLOY BLOCKED: lint errors detected. Fix them before deploying."
    exit 1
fi

# Run test suite
echo "Running tests..."
sudo -u cryptobot .venv/bin/python -m pytest tests/ -q --tb=short
if [ $? -ne 0 ]; then
    echo "DEPLOY BLOCKED: tests failed. Fix them before deploying."
    exit 1
fi

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
