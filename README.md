# CryptoBot

Automated trading bot for the Base chain (Ethereum L2). Uses web3.py to interact with Uniswap V3 and executes trades based on a configurable strategy.

---

## Architecture

```
main.py                  → Entry point. Connects to Base RPC, runs the strategy on a 60s schedule.
bot/
  config.py              → Chain config, contract addresses, trading parameters
  wallet.py              → Wallet management (sign, send, balance checks)
  dex.py                 → Uniswap V3 integration (quote + swap)
  strategy.py            → Trading strategy (SMA crossover — swap ETH ↔ USDC on signal)
  logger.py              → Colored console output + log file
tests/                   → Test suite
```

The bot polls on a 60-second interval. Each tick it:
1. Fetches the current ETH/USDC price via the Uniswap V3 Quoter
2. Appends to the price history
3. Computes short (5-period) and long (20-period) simple moving averages
4. Executes a swap if a crossover signal is detected

---

## Requirements

- Python 3.11+
- A Base wallet with ETH (for gas) and tokens to trade
- A Base mainnet RPC endpoint (public or via Alchemy/Infura)

---

## Setup

```bash
# 1. Create and activate virtual environment
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS/Linux

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
copy .env.example .env        # Windows
# cp .env.example .env        # macOS/Linux
# Then edit .env with your values (see Configuration below)

# 4. Run
python main.py
```

---

## Configuration

Edit `.env` (never commit this file):

| Variable | Description | Default |
|---|---|---|
| `BASE_RPC_URL` | Base mainnet RPC endpoint | `https://mainnet.base.org` |
| `PRIVATE_KEY` | Your wallet private key (0x...) | — |
| `MAX_TRADE_SIZE_ETH` | Max ETH per trade | `0.01` |
| `SLIPPAGE_TOLERANCE` | Slippage as a decimal (0.005 = 0.5%) | `0.005` |
| `LOG_LEVEL` | Logging verbosity (DEBUG/INFO/WARNING/ERROR) | `INFO` |

For better rate limits, replace `BASE_RPC_URL` with an Alchemy or Infura endpoint:
```
BASE_RPC_URL=https://base-mainnet.g.alchemy.com/v2/YOUR_API_KEY
```

---

## Chain & Contract Addresses (Base Mainnet)

| Name | Address |
|---|---|
| WETH | `0x4200000000000000000000000000000000000006` |
| USDC | `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913` |
| Uniswap V3 Router | `0x2626664c2603336E57B271c5C0b26F421741e481` |
| Uniswap V3 Factory | `0x33128a8fC17869897dcE68Ed026d694621f6FDfD` |
| Uniswap V3 Quoter | `0x3d4e44Eb1374240CE5F1B136041F7C384e07db63` |
| Chain ID | `8453` |

**For testnet (Base Sepolia):** set `BASE_RPC_URL=https://sepolia.base.org` and `BASE_CHAIN_ID = 84532` in `bot/config.py`.

---

## Strategy

The default strategy in `bot/strategy.py` is a **simple moving average (SMA) crossover**:

- **Buy signal:** 5-period SMA crosses above 20-period SMA → swap ETH → USDC
- **Sell signal:** 5-period SMA crosses below 20-period SMA → swap USDC → ETH
- **Hold:** no crossover detected

To use a custom strategy, extend or replace `SimpleMAStrategy` in `bot/strategy.py`. The `run_once()` method is called each tick.

---

## Security

- **Never commit `.env`** — it contains your private key. It is listed in `.gitignore`.
- **Never push to a public repository.** If using GitHub, always create the repo as **Private**.
- Keep `MAX_TRADE_SIZE_ETH` small while testing to limit exposure.
- Test on **Base Sepolia testnet** before running on mainnet.

---

## Base MCP (optional)

The Base MCP server (`https://mcp.base.org`) can be used alongside this bot for manual operations via Claude Code:

```bash
claude mcp add --transport http base-mcp https://mcp.base.org
```

Supports: `send`, `swap`, `deposit`, `borrow`, `repay`, `sign-message`. Each action requires approval via Base Account.

---

## Transaction Records (Tax)

Every swap is automatically recorded to `records/transactions.csv` with the following fields:

| Field | Description |
|---|---|
| `date_utc` | Timestamp in UTC |
| `tx_hash` | On-chain transaction hash |
| `type` | Always `swap` |
| `token_in` | Contract address of token sold |
| `amount_in` | Amount sold |
| `token_out` | Contract address of token bought |
| `amount_out` | Amount received (quoted, not slippage-adjusted) |
| `price_eth_usd` | ETH/USD price at time of trade |
| `gas_used` | Gas consumed |
| `gas_price_gwei` | Gas price in Gwei |
| `gas_cost_eth` | Total gas fee in ETH |
| `status` | `success`, `failed`, `pending`, or `unknown` |

This CSV can be imported directly into tax software such as **Koinly**, **CoinTracker**, or **TaxBit**.

> **Important:** `records/` is gitignored and will not be committed. Back up this directory externally (cloud storage, external drive) to ensure you don't lose your tax records.

---

## Logs

Logs are written to `logs/bot.log` and printed to the console. The `logs/` directory is gitignored.
