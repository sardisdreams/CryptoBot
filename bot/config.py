import os
from dotenv import load_dotenv

load_dotenv()

# Base chain
BASE_CHAIN_ID = 8453
BASE_RPC_URL = os.getenv("BASE_RPC_URL", "https://mainnet.base.org")

PRIVATE_KEY = os.getenv("PRIVATE_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# Uniswap V3 on Base
UNISWAP_V3_ROUTER = "0x2626664c2603336E57B271c5C0b26F421741e481"
UNISWAP_V3_FACTORY = "0x33128a8fC17869897dcE68Ed026d694621f6FDfD"
UNISWAP_V3_QUOTER = "0x3d4e44Eb1374240CE5F1B136041F7C384e47db63"

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
SLIPPAGE_TOLERANCE = float(os.getenv("SLIPPAGE_TOLERANCE", "0.005"))
GAS_LIMIT = 300_000

# Capital deployment limits
MAX_DEPLOY_USD = 200.0    # never deploy more than this total across all open positions
MIN_TRADE_USD  = 20.0     # minimum trade size (below this, gas isn't worth it)
MAX_TRADE_USD  = 75.0     # max single trade size during testing phase

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
