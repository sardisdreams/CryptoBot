# Wallet Migration & Setup Guide

## Two-wallet architecture

| Bot | Wallet | Chain | Purpose |
|-----|--------|-------|---------|
| Base bot (main.py) | Wallet A | Base L2 | Altcoin momentum trading |
| Hyperliquid bot (hl_main.py) | Wallet B | Hyperliquid L1 | BTC/ETH/SOL perps, 2x leverage |

Never share private keys between bots. Each bot has its own isolated capital.

---

## Migrating the Base bot to a new wallet

1. **Create new wallet** (MetaMask, Rabby, or `cast wallet new`)
2. **Fund it**: transfer USDC from old wallet to new wallet on Base
3. **Update server .env**:
   ```
   ssh root@143.198.37.28
   nano /opt/cryptobot/app/.env
   # Replace PRIVATE_KEY=0x<old> with PRIVATE_KEY=0x<new>
   ```
4. **Clear old position records** (new wallet = fresh start):
   ```
   echo '{}' > /opt/cryptobot/app/data/positions.json
   echo '{}' > /opt/cryptobot/app/data/capital.json
   ```
5. **Restart**:
   ```
   systemctl restart cryptobot
   ```
6. **Verify** in dashboard: wallet address shows the new address

---

## Setting up the Hyperliquid bot (new wallet)

### Step 1 — Create the HL wallet
Create a new EVM wallet (same format as Ethereum/Base).
This is separate from your Base wallet — never reuse keys.

### Step 2 — Fund the wallet on Hyperliquid
Hyperliquid accepts deposits from Arbitrum:
1. Bridge USDC from Base → Arbitrum  
   Use: https://bridge.arbitrum.io or https://www.relay.link
2. Deposit USDC from Arbitrum to Hyperliquid  
   Go to: https://app.hyperliquid.xyz → Transfer → Deposit
3. Confirm the deposit appears in your HL account

Recommended starting amount: $500–$1,000 USDC

### Step 3 — Add private key to server
```bash
ssh root@143.198.37.28
echo 'HL_PRIVATE_KEY=0x<your_new_wallet_private_key>' >> /opt/cryptobot/app/.env
```

### Step 4 — Run setup script
```bash
ssh root@143.198.37.28 'bash /opt/cryptobot/app/deploy/setup_hl.sh'
```

This installs the SDK, registers the service, and starts the bot.

### Step 5 — Verify
```bash
journalctl -u hl_cryptobot -f
```
You should see:
```
HL tick | v1.0 | HH:MM UTC
HL account: $XXX.XX | 0/2 positions | risk_ok=True
Signal BTC: 70/100 LONG | RSI=48 ...
```

---

## .env reference

```
# Base bot
PRIVATE_KEY=0x<base_wallet_private_key>
BASE_RPC_URL=https://base-mainnet.g.alchemy.com/v2/<key>

# Hyperliquid bot
HL_PRIVATE_KEY=0x<hl_wallet_private_key>

# Shared
ANTHROPIC_API_KEY=sk-ant-...
ALERT_EMAIL=admin@sardisdreams.com
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=...
SMTP_PASS=...

# Dashboard
DASHBOARD_USER=admin
DASHBOARD_PASS=...

# Budgets
ANTHROPIC_BUDGET_USD=30
HL_ANTHROPIC_BUDGET_USD=15
```
