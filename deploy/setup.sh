#!/bin/bash
# Run this once on a fresh DigitalOcean Ubuntu 22.04 droplet
# Usage: bash setup.sh
set -e

echo "=== CryptoBot Server Setup ==="

# System updates
apt-get update -y && apt-get upgrade -y

# Install Python 3.11, git, pip
apt-get install -y python3.11 python3.11-venv python3-pip git ufw curl

# Firewall — allow SSH and dashboard port only
ufw allow OpenSSH
ufw allow 5000/tcp   # dashboard
ufw --force enable
echo "Firewall configured"

# Create app user (don't run as root)
if ! id -u cryptobot &>/dev/null; then
    useradd -m -s /bin/bash cryptobot
    echo "Created user: cryptobot"
fi

# Create app directory
mkdir -p /opt/cryptobot
chown cryptobot:cryptobot /opt/cryptobot

# Clone repo (you'll be prompted for GitHub credentials or use SSH key)
echo ""
echo "=== Cloning repository ==="
echo "Enter your GitHub repo URL (e.g. https://github.com/YOUR_USER/CryptoBot.git):"
read REPO_URL
sudo -u cryptobot git clone "$REPO_URL" /opt/cryptobot/app

# Set up Python virtual environment
echo "=== Setting up Python environment ==="
cd /opt/cryptobot/app
sudo -u cryptobot python3.11 -m venv .venv
sudo -u cryptobot .venv/bin/pip install --upgrade pip
sudo -u cryptobot .venv/bin/pip install -r requirements.txt

# Create data directories
sudo -u cryptobot mkdir -p /opt/cryptobot/app/data
sudo -u cryptobot mkdir -p /opt/cryptobot/app/records
sudo -u cryptobot mkdir -p /opt/cryptobot/app/logs

# Create .env from template
echo ""
echo "=== Environment Setup ==="
echo "Copying .env.example — you MUST edit /opt/cryptobot/app/.env with real values"
sudo -u cryptobot cp /opt/cryptobot/app/.env.example /opt/cryptobot/app/.env
echo "Edit .env now: nano /opt/cryptobot/app/.env"
echo ""

# Install systemd services
cp /opt/cryptobot/app/deploy/cryptobot.service /etc/systemd/system/
cp /opt/cryptobot/app/deploy/cryptobot-dashboard.service /etc/systemd/system/
systemctl daemon-reload

# Enable and start services
systemctl enable cryptobot
systemctl enable cryptobot-dashboard

echo ""
echo "=== Setup complete! ==="
echo ""
echo "Next steps:"
echo "  1. Edit /opt/cryptobot/app/.env with your keys"
echo "  2. Edit /opt/cryptobot/app/.env.dashboard with dashboard password"
echo "  3. Run: systemctl start cryptobot"
echo "  4. Run: systemctl start cryptobot-dashboard"
echo "  5. Check status: systemctl status cryptobot"
echo "  6. View logs: journalctl -u cryptobot -f"
echo ""
echo "Dashboard will be at: http://$(curl -s ifconfig.me):5000"
