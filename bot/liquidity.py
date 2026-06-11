"""
Checks whether a token has sufficient liquidity on Base to trade safely.
Tests the TOKEN→USDC SELL direction with a $20 test quote — this is the direction
that matters most, since thin sell pools are how profitable positions turn into losses.
Results cached 24h so on-chain quote is only made once per token per day.
"""
import json
import os
import time
from web3 import Web3
from bot.logger import setup_logger
from bot.config import UNISWAP_V3_QUOTER

logger = setup_logger("liquidity")

CACHE_FILE       = "data/liquidity_cache.json"
CACHE_TTL_HOURS  = 24
TEST_AMOUNT_USD  = 20.0   # USD value of tokens to simulate selling
MAX_PRICE_IMPACT = 0.15   # 15% max sell-side price impact — above this, pool is too thin to exit safely

USDC_ADDRESS  = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
USDC_DECIMALS = 6

# Minimal ABI for Uniswap V3 Quoter — only quoteExactInputSingle needed
_V3_QUOTER_ABI = [{
    "inputs": [{"components": [
        {"name": "tokenIn",           "type": "address"},
        {"name": "tokenOut",          "type": "address"},
        {"name": "amountIn",          "type": "uint256"},
        {"name": "fee",               "type": "uint24"},
        {"name": "sqrtPriceLimitX96", "type": "uint160"},
    ], "name": "params", "type": "tuple"}],
    "name": "quoteExactInputSingle",
    "outputs": [
        {"name": "amountOut",                  "type": "uint256"},
        {"name": "sqrtPriceX96After",          "type": "uint160"},
        {"name": "initializedTicksCrossed",    "type": "uint32"},
        {"name": "gasEstimate",                "type": "uint256"},
    ],
    "stateMutability": "nonpayable",
    "type": "function",
}]

ZERO_ADDR = "0x0000000000000000000000000000000000000000"


def _load_cache() -> dict:
    if not os.path.exists(CACHE_FILE):
        return {}
    with open(CACHE_FILE) as f:
        return json.load(f)


def _save_cache(data: dict):
    os.makedirs("data", exist_ok=True)
    tmp = CACHE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, CACHE_FILE)


def _is_stale(entry: dict) -> bool:
    cached_at = entry.get("cached_at", 0)
    return (time.time() - cached_at) > (CACHE_TTL_HOURS * 3600)


def _v3_sell_quote(w3: Web3, token_address: str, test_tokens_wei: int) -> int:
    """Return USDC amount out from selling test_tokens_wei of token via best V3 fee tier. 0 if no pool."""
    try:
        quoter = w3.eth.contract(
            address=Web3.to_checksum_address(UNISWAP_V3_QUOTER), abi=_V3_QUOTER_ABI
        )
        for fee in (500, 3000, 10000):
            try:
                result = quoter.functions.quoteExactInputSingle({
                    "tokenIn":           Web3.to_checksum_address(token_address),
                    "tokenOut":          Web3.to_checksum_address(USDC_ADDRESS),
                    "amountIn":          test_tokens_wei,
                    "fee":               fee,
                    "sqrtPriceLimitX96": 0,
                }).call({"from": ZERO_ADDR})
                if result[0] > 0:
                    return result[0]
            except Exception:
                continue
    except Exception as e:
        logger.debug(f"V3 sell quote failed: {e}")
    return 0


def _aerodrome_sell_quote(w3: Web3, token_address: str, test_tokens_wei: int) -> int:
    """Return USDC amount out from selling test_tokens_wei of token via Aerodrome. 0 if no pool."""
    try:
        from bot.aerodrome import AerodromeRouter

        class _FakeWallet:
            address = ZERO_ADDR

        router = AerodromeRouter(w3, _FakeWallet())
        amount_out, _ = router.get_quote(token_address, USDC_ADDRESS, test_tokens_wei)
        return amount_out
    except Exception as e:
        logger.debug(f"Aerodrome sell quote failed: {e}")
    return 0


def check_base_liquidity(
    w3: Web3,
    token_address: str,
    token_symbol: str,
    token_price_usd: float,
    token_decimals: int = 18,
) -> dict:
    """
    Simulates selling $TEST_AMOUNT_USD worth of TOKEN back to USDC on Base.
    Returns {"liquid": bool, "price_impact": float, "reason": str}
    Cached 24h — only performs on-chain quotes once per token per day.
    """
    cache = _load_cache()
    key   = token_address.lower()

    if key in cache and not _is_stale(cache[key]):
        result = cache[key]
        logger.info(
            f"Liquidity cache hit: {token_symbol} — "
            f"{'OK' if result['liquid'] else 'SKIP'} ({result['reason']})"
        )
        return result

    if token_price_usd <= 0:
        result = {
            "liquid": False, "price_impact": 1.0,
            "reason": "No price data — cannot simulate sell",
            "cached_at": time.time(),
        }
        cache[key] = result
        _save_cache(cache)
        return result

    # How many tokens represent $TEST_AMOUNT_USD at market price?
    test_tokens     = TEST_AMOUNT_USD / token_price_usd
    test_tokens_wei = int(test_tokens * 10 ** token_decimals)

    if test_tokens_wei <= 0:
        result = {
            "liquid": False, "price_impact": 1.0,
            "reason": f"Token price ${token_price_usd:,.4f} too high for ${TEST_AMOUNT_USD:.0f} test lot",
            "cached_at": time.time(),
        }
        cache[key] = result
        _save_cache(cache)
        return result

    # Try both DEXes — take the best (lowest impact) quote
    v3_out   = _v3_sell_quote(w3, token_address, test_tokens_wei)
    aero_out = _aerodrome_sell_quote(w3, token_address, test_tokens_wei)
    best_out = max(v3_out, aero_out)

    if best_out == 0:
        dex_label = "V3 or Aerodrome"
        result = {
            "liquid": False, "price_impact": 1.0,
            "reason": f"No sell pool found on {dex_label} for {token_symbol}→USDC",
            "cached_at": time.time(),
        }
        logger.info(f"Liquidity check {token_symbol}: FAIL — {result['reason']}")
        cache[key] = result
        _save_cache(cache)
        return result

    received_usd = best_out / (10 ** USDC_DECIMALS)
    impact       = (TEST_AMOUNT_USD - received_usd) / TEST_AMOUNT_USD
    liquid       = impact <= MAX_PRICE_IMPACT
    dex_used     = "V3" if v3_out >= aero_out else "Aerodrome"

    if liquid:
        reason = f"OK — {impact:.1%} sell impact on ${TEST_AMOUNT_USD:.0f} test ({dex_used})"
    else:
        reason = (
            f"Sell price impact {impact:.1%} via {dex_used} — "
            f"exceeds {MAX_PRICE_IMPACT:.0%} max. Pool too thin to exit safely."
        )

    result = {
        "liquid":       liquid,
        "price_impact": round(impact, 4),
        "reason":       reason,
        "dex":          dex_used,
        "cached_at":    time.time(),
    }

    logger.info(f"Liquidity check {token_symbol}: {'PASS' if liquid else 'FAIL'} — {reason}")
    cache[key] = result
    _save_cache(cache)
    return result


def filter_liquid_coins(w3: Web3, coins: list[dict], prices: dict) -> list[dict]:
    """
    Filter screener coins to only those with real Base sell-side liquidity.
    Known high-liquidity registry tokens skip the check entirely.
    Coins whose contract address is not yet known pass through unverified
    (they will be checked again at get_token_info time before any buy).
    """
    from bot.config import HIGH_LIQUIDITY_TOKENS
    from bot.token_cache import get as get_cached_token

    liquid = []
    for coin in coins:
        sym = coin.get("symbol", "").upper()

        if sym in HIGH_LIQUIDITY_TOKENS:
            liquid.append(coin)
            continue

        cg_id  = coin.get("cg_id", "")
        cached = get_cached_token(cg_id)
        if not cached:
            # Contract address not yet known — include but mark unverified.
            # The liquidity gate in get_token_info will check before any buy.
            coin["liquidity_verified"] = False
            liquid.append(coin)
            continue

        address  = cached["address"]
        decimals = cached.get("decimals", 18)
        price    = prices.get(sym, 0) or cached.get("price", 0)

        result = check_base_liquidity(w3, address, sym, price, decimals)
        coin["liquidity_verified"] = result["liquid"]
        coin["price_impact"]       = result.get("price_impact", 0)
        coin["liquidity_reason"]   = result["reason"]

        if result["liquid"]:
            liquid.append(coin)
        else:
            logger.info(f"Screener filtered out {sym}: {result['reason']}")

    return liquid
