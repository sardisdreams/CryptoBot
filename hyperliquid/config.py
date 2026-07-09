"""
Hyperliquid perpetuals bot — configuration.
All secrets come from environment variables, never hardcoded.
"""
import os
from dotenv import load_dotenv

load_dotenv()

HL_BOT_VERSION = "v1.0"

# ── Wallet ────────────────────────────────────────────────────────────────────
# Separate private key from the Base bot wallet — never share keys between bots.
HL_PRIVATE_KEY = os.getenv("HL_PRIVATE_KEY", "")

# ── Markets ───────────────────────────────────────────────────────────────────
# Coins traded as perpetual contracts on Hyperliquid.
# These are the most liquid, lowest-manipulation perps on the exchange.
COINS = ["BTC", "ETH", "SOL"]

# ── Leverage ──────────────────────────────────────────────────────────────────
# 2x conservative. At 2x, liquidation requires a 50% adverse move — well outside
# our stop-loss. Never raise above 3x without human review.
LEVERAGE = 2

# ── Position sizing ───────────────────────────────────────────────────────────
# Each trade uses this fraction of available margin (USDC on HL).
# 25% per trade, max 2 open positions = 50% deployed max.
TRADE_SIZE_PCT   = 0.25
MIN_TRADE_USD    = 30.0
MAX_TRADE_USD    = 300.0
MAX_OPEN         = 2      # max simultaneous open positions across all coins

# ── Exit targets (% of NOTIONAL, not margin) ─────────────────────────────────
# With 2x leverage: 5% notional TP = 10% return on margin.
# Risk:reward is 5%:3% = 1.67:1, needs >37.5% win rate to be profitable.
TP_PCT           = 5.0    # take-profit: 5% from entry
SL_PCT           = 3.0    # stop-loss:   3% from entry
MAX_HOLD_HOURS   = 48     # time stop: close if held longer with no TP/SL

# ── Signal thresholds ─────────────────────────────────────────────────────────
SIGNAL_MIN_ENTRY  = 50    # minimum score to qualify as a candidate
AUTO_EXEC_MIN     = 65    # auto-execute without Claude above this score
CLAUDE_REVIEW_MIN = 50    # call Claude for borderline signals in this range

# ── Risk guards ───────────────────────────────────────────────────────────────
DAILY_DRAWDOWN_LIMIT = 0.05   # halt if down >5% today (leverage amplifies losses)
WIN_RATE_MIN         = 0.35   # pause if <35% win rate over last 8 trades
WIN_RATE_LOOKBACK    = 8
COOLDOWN_MINUTES     = 30     # no re-entry into same coin for 30min after SL

# ── API ───────────────────────────────────────────────────────────────────────
HL_API_URL   = "https://api.hyperliquid.xyz"
HL_CANDLE_INTERVAL = "1h"     # 1-hour candles for intraday signals
HL_CANDLE_LOOKBACK = 100      # number of candles to fetch for indicators

# ── Operational ───────────────────────────────────────────────────────────────
TICK_INTERVAL_SECONDS = 900   # 15 minutes between ticks
ANTHROPIC_BUDGET_USD  = float(os.getenv("HL_ANTHROPIC_BUDGET_USD", "15"))


def validate():
    if not HL_PRIVATE_KEY:
        raise SystemExit("HL_PRIVATE_KEY not set in .env — cannot start Hyperliquid bot")
