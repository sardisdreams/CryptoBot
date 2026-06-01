import os
from dotenv import load_dotenv

load_dotenv()

# Base chain
BASE_CHAIN_ID = 8453
BASE_RPC_URL = os.getenv("BASE_RPC_URL", "https://mainnet.base.org")

PRIVATE_KEY = os.getenv("PRIVATE_KEY")

# Uniswap V3 on Base
UNISWAP_V3_ROUTER = "0x2626664c2603336E57B271c5C0b26F421741e481"
UNISWAP_V3_FACTORY = "0x33128a8fC17869897dcE68Ed026d694621f6FDfD"
UNISWAP_V3_QUOTER = "0x3d4e44Eb1374240CE5F1B136041F7C384e07db63"

# Common Base token addresses
WETH = "0x4200000000000000000000000000000000000006"
USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"

# Trading parameters
MAX_TRADE_SIZE_ETH = float(os.getenv("MAX_TRADE_SIZE_ETH", "0.01"))
SLIPPAGE_TOLERANCE = float(os.getenv("SLIPPAGE_TOLERANCE", "0.005"))  # 0.5%
GAS_LIMIT = 300_000

def validate():
    if not PRIVATE_KEY:
        raise ValueError("PRIVATE_KEY is not set in .env")
    if not BASE_RPC_URL:
        raise ValueError("BASE_RPC_URL is not set in .env")
