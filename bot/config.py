import os
from dotenv import load_dotenv

load_dotenv()

BOT_VERSION = "v1.1"

# Base chain
BASE_CHAIN_ID = 8453
BASE_RPC_URL = os.getenv("BASE_RPC_URL", "https://mainnet.base.org")

PRIVATE_KEY = os.getenv("PRIVATE_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# Uniswap V3 on Base
UNISWAP_V3_ROUTER = "0x2626664c2603336E57B271c5C0b26F421741e481"
UNISWAP_V3_FACTORY = "0x33128a8fC17869897dcE68Ed026d694621f6FDfD"
UNISWAP_V3_QUOTER = "0x3d4e44Eb1374240CE5F1B136041F7C384e47db63"

# Aerodrome on Base (primary DEX for most Base-native tokens)
AERODROME_ROUTER  = "0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43"
AERODROME_FACTORY = "0x420DD381b31aEf6683db6B902084cB0FFECe40Da"

# Token registry — add any Base token here
TOKENS = {
    "WETH":  {"address": "0x4200000000000000000000000000000000000006", "decimals": 18, "symbol": "WETH"},
    "USDC":  {"address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", "decimals": 6,  "symbol": "USDC"},
    "USDT":  {"address": "0xfde4C96c8593536E31F229EA8f37b2ADa2699bb2", "decimals": 6,  "symbol": "USDT"},
    "DAI":   {"address": "0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb", "decimals": 18, "symbol": "DAI"},
    "cbBTC": {"address": "0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf", "decimals": 8,  "symbol": "cbBTC"},
    "cbETH": {"address": "0x2Ae3F1Ec7F1F5012CFEab0185bfc7aa3cf0DEc22", "decimals": 18, "symbol": "cbETH"},
}

# Stable coins (used to determine USD value without a swap quote)
STABLECOINS = {"USDC", "USDT", "DAI"}

# Base token used for gas and as the default quote currency
WETH_ADDRESS = TOKENS["WETH"]["address"]
USDC_ADDRESS = TOKENS["USDC"]["address"]

# Trading parameters
SLIPPAGE_TOLERANCE        = float(os.getenv("SLIPPAGE_TOLERANCE", "0.005"))  # major tokens (0.5%)
SLIPPAGE_TOLERANCE_LOWCAP = 0.03   # 3% for low-liquidity Base-native tokens
SLIPPAGE_MAX              = 0.05   # 5% absolute maximum — never exceed this
MAX_PRICE_IMPACT          = 0.05   # reject trade if DEX price is >5% worse than market price
GAS_LIMIT = 700_000                # increased from 400k — 2-hop Aerodrome swaps need ~400-450k gas

# High-liquidity tokens that can use tight slippage
HIGH_LIQUIDITY_TOKENS = {"WETH", "USDC", "USDT", "DAI", "cbBTC", "cbETH"}

# Swing trading targets — volatile coins with tight TP/SL for quick in/out
SWING_TARGETS = {
    "VVV": {
        "cg_id":          "venice-token",
        "address":        "0xacfE6019Ed1A7Dc6f7B508C02d1b04ec88cC21bf",
        "decimals":       18,
        "take_profit_pct": 8.0,   # sell quickly at +8%
        "stop_loss_pct":   8.0,   # cut fast at -8%
        "max_hold_hours":  18.0,  # don't hold overnight if no move
        "description":    "Base-native AI privacy platform. High volatility — 7d range $14-20. Swing trade only.",
        "weekly_range_low":  14.68,
        "weekly_range_high": 19.93,
    },
}

# Capital deployment limits — these are fallback defaults only.
# Actual limits are computed dynamically by bot/capital.py each cycle.
MAX_DEPLOY_USD = 300.0    # fallback cap if capital.py unavailable
MIN_TRADE_USD  = 20.0     # absolute minimum trade (gas floor)
MAX_TRADE_USD  = 100.0    # absolute maximum single trade

# Pool fee tiers: 500 = 0.05%, 3000 = 0.3%, 10000 = 1%
DEFAULT_FEE = 3000


def validate():
    missing = []
    if not PRIVATE_KEY:
        missing.append("PRIVATE_KEY")
    if not ANTHROPIC_API_KEY:
        missing.append("ANTHROPIC_API_KEY")
    if missing:
        raise ValueError(f"Missing required .env variables: {', '.join(missing)}")
