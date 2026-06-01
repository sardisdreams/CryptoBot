"""
Checks whether a token has sufficient liquidity on Base to trade.
Uses a small Aerodrome test quote ($20 USDC) to measure price impact.
Results are cached for 24 hours so we don't re-check every tick.
"""
import json
import os
import time
from web3 import Web3
from bot.logger import setup_logger

logger = setup_logger("liquidity")

CACHE_FILE       = "data/liquidity_cache.json"
CACHE_TTL_HOURS  = 24
TEST_AMOUNT_USDC = 20.0   # test quote size in USD
MAX_PRICE_IMPACT = 0.10   # 10% max price impact on test quote — if worse, skip token

USDC_ADDRESS = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
USDC_DECIMALS = 6


def _load_cache() -> dict:
    if not os.path.exists(CACHE_FILE):
        return {}
    with open(CACHE_FILE) as f:
        return json.load(f)


def _save_cache(data: dict):
    os.makedirs("data", exist_ok=True)
    with open(CACHE_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _is_stale(entry: dict) -> bool:
    cached_at = entry.get("cached_at", 0)
    return (time.time() - cached_at) > (CACHE_TTL_HOURS * 3600)


def check_base_liquidity(
    w3: Web3,
    token_address: str,
    token_symbol: str,
    token_price_usd: float,
    token_decimals: int = 18,
) -> dict:
    """
    Returns {"liquid": bool, "price_impact": float, "reason": str}
    Uses cached result if available and fresh.
    """
    cache = _load_cache()
    key   = token_address.lower()

    if key in cache and not _is_stale(cache[key]):
        result = cache[key]
        logger.info(f"Liquidity cache hit: {token_symbol} — {'OK' if result['liquid'] else 'SKIP'} ({result['reason']})")
        return result

    # Minimum volume sanity check before trying on-chain quote
    if token_price_usd <= 0:
        result = {"liquid": False, "price_impact": 1.0, "reason": "No price data", "cached_at": time.time()}
        cache[key] = result
        _save_cache(cache)
        return result

    # Test quote: $20 USDC → TOKEN on Aerodrome
    try:
        from bot.aerodrome import AerodromeRouter

        class _FakeWallet:
            address = "0x0000000000000000000000000000000000000000"

        router = AerodromeRouter(w3, _FakeWallet())
        test_usdc_wei  = int(TEST_AMOUNT_USDC * 10 ** USDC_DECIMALS)
        amount_out, _  = router.get_quote(USDC_ADDRESS, token_address, test_usdc_wei)

        if amount_out == 0:
            result = {"liquid": False, "price_impact": 1.0, "reason": "No Aerodrome pool found on Base"}
        else:
            received_tokens = amount_out / (10 ** token_decimals)
            received_usd    = received_tokens * token_price_usd
            impact          = (TEST_AMOUNT_USDC - received_usd) / TEST_AMOUNT_USDC

            if impact > MAX_PRICE_IMPACT:
                result = {
                    "liquid":       False,
                    "price_impact": round(impact, 4),
                    "reason":       f"Price impact {impact:.1%} on ${TEST_AMOUNT_USDC:.0f} test — thin pool",
                }
            else:
                result = {
                    "liquid":       True,
                    "price_impact": round(impact, 4),
                    "reason":       f"OK — {impact:.1%} price impact on ${TEST_AMOUNT_USDC:.0f} test",
                }

        logger.info(f"Liquidity check {token_symbol}: {'PASS' if result['liquid'] else 'FAIL'} — {result['reason']}")

    except Exception as e:
        logger.warning(f"Liquidity check failed for {token_symbol}: {e}")
        result = {"liquid": True, "price_impact": 0.0, "reason": f"Check failed ({e}) — allowing"}

    result["cached_at"] = time.time()
    cache[key] = result
    _save_cache(cache)
    return result


def filter_liquid_coins(w3: Web3, coins: list[dict], prices: dict) -> list[dict]:
    """
    Filter a list of screener coins to only those with real Base liquidity.
    Known high-liquidity tokens skip the check entirely.
    """
    from bot.config import HIGH_LIQUIDITY_TOKENS
    from bot.token_cache import get as get_cached_token

    liquid = []
    for coin in coins:
        sym = coin.get("symbol", "").upper()

        # Known liquid tokens — skip check
        if sym in HIGH_LIQUIDITY_TOKENS:
            liquid.append(coin)
            continue

        # Need contract address — check token cache
        cg_id  = coin.get("cg_id", "")
        cached = get_cached_token(cg_id)
        if not cached:
            # No contract address known yet — include but mark unverified
            coin["liquidity_verified"] = False
            liquid.append(coin)
            continue

        address  = cached["address"]
        decimals = cached.get("decimals", 18)
        price    = prices.get(sym, cached.get("price", 0))

        result = check_base_liquidity(w3, address, sym, price, decimals)
        coin["liquidity_verified"] = result["liquid"]
        coin["price_impact"]       = result.get("price_impact", 0)
        coin["liquidity_reason"]   = result["reason"]

        if result["liquid"]:
            liquid.append(coin)
        else:
            logger.info(f"Filtered out {sym}: {result['reason']}")

    return liquid
