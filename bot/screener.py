import requests
import certifi
from bot.logger import setup_logger

logger = setup_logger("screener")

SSL = certifi.where()

COINGECKO_MARKETS   = "https://api.coingecko.com/api/v3/coins/markets"
COINGECKO_NEW       = "https://api.coingecko.com/api/v3/coins/list/new"
COINGECKO_GAINERS   = "https://api.coingecko.com/api/v3/coins/top_gainers_losers"
DEFILLAMA_CHAINS    = "https://api.llama.fi/chains"
DEFILLAMA_PROTOCOLS = "https://api.llama.fi/protocols"


def get_base_ecosystem_coins(min_market_cap: int = 5_000_000, max_market_cap: int = 500_000_000) -> list[dict]:
    """
    Find coins in the Base ecosystem within a market cap range.
    Targets the $5M–$500M sweet spot for meaningful upside.
    """
    try:
        resp = requests.get(
            COINGECKO_MARKETS,
            params={
                "vs_currency": "usd",
                "category": "base-ecosystem",
                "order": "volume_desc",
                "per_page": 50,
                "price_change_percentage": "1h,24h,7d",
            },
            timeout=15,
            verify=SSL,
        )
        resp.raise_for_status()
        coins = resp.json()

        filtered = []
        for coin in coins:
            mc = coin.get("market_cap") or 0
            if mc < min_market_cap or mc > max_market_cap:
                continue
            filtered.append({
                "symbol":       coin.get("symbol", "").upper(),
                "name":         coin.get("name"),
                "market_cap":   mc,
                "price":        coin.get("current_price", 0),
                "change_1h":    coin.get("price_change_percentage_1h_in_currency", 0),
                "change_24h":   coin.get("price_change_percentage_24h", 0),
                "change_7d":    coin.get("price_change_percentage_7d_in_currency", 0),
                "volume_24h":   coin.get("total_volume", 0),
                "cg_id":        coin.get("id"),
            })

        logger.info(f"Base ecosystem coins found: {len(filtered)} (${min_market_cap/1e6:.0f}M–${max_market_cap/1e6:.0f}M mcap)")
        return filtered

    except Exception as e:
        logger.warning(f"Base ecosystem fetch failed: {e}")
        return []


def get_top_gainers(hours: int = 24) -> list[dict]:
    """Top gaining coins in the last N hours — catch momentum early."""
    try:
        resp = requests.get(
            COINGECKO_GAINERS,
            params={"vs_currency": "usd", "duration": f"{hours}h", "top_coins": 500},
            timeout=10,
            verify=SSL,
        )
        resp.raise_for_status()
        data = resp.json()
        gainers = data.get("top_gainers", [])[:10]
        result = []
        for c in gainers:
            result.append({
                "symbol":    c.get("symbol", "").upper(),
                "name":      c.get("name"),
                "change":    c.get("usd_24h_change", 0),
                "price":     c.get("usd", 0),
                "volume":    c.get("usd_24h_vol", 0),
                "cg_id":     c.get("id"),
            })
        logger.info(f"Top gainers ({hours}h): {[c['symbol'] for c in result]}")
        return result
    except Exception as e:
        logger.warning(f"Top gainers fetch failed: {e}")
        return []


def get_defillama_base_protocols() -> list[dict]:
    """
    Get DeFi protocols with TVL on Base, sorted by TVL change.
    Rising TVL = growing adoption = potential token upside.
    """
    try:
        resp = requests.get(DEFILLAMA_PROTOCOLS, timeout=15, verify=SSL)
        resp.raise_for_status()
        protocols = resp.json()

        base_protocols = []
        for p in protocols:
            chains = p.get("chains", [])
            if "Base" not in chains:
                continue
            tvl = p.get("tvl", 0) or 0
            change_1d = p.get("change_1d") or 0
            if tvl < 100_000:  # Skip tiny protocols
                continue
            base_protocols.append({
                "name":      p.get("name"),
                "symbol":    p.get("symbol", "").upper() if p.get("symbol") else None,
                "tvl":       tvl,
                "change_1d": change_1d,
                "category":  p.get("category"),
            })

        # Sort by 1d TVL change (biggest movers first)
        base_protocols.sort(key=lambda x: x["change_1d"] or 0, reverse=True)
        logger.info(f"Base DeFi protocols: {len(base_protocols)} found")
        return base_protocols[:20]

    except Exception as e:
        logger.warning(f"DeFiLlama protocols fetch failed: {e}")
        return []


def get_screening_report() -> dict:
    """Full screening report — all discovery signals in one call."""
    return {
        "base_ecosystem":    get_base_ecosystem_coins(),
        "top_gainers_24h":   get_top_gainers(24),
        "defillama_base":    get_defillama_base_protocols(),
    }
