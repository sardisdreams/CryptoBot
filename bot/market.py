import time as _time_mod
import requests
import certifi
from bot.config import TOKENS, STABLECOINS
from bot.logger import setup_logger

logger = setup_logger("market")

_RATE_LIMIT_UNTIL: float = 0  # epoch time until which CoinGecko calls should be paused


def _cg_get(url: str, params: dict, timeout: int = 10) -> requests.Response | None:
    """
    CoinGecko GET with exponential backoff on 429 rate limit errors.
    Returns None if still rate-limited and the cached result should be used.
    """
    global _RATE_LIMIT_UNTIL
    if _time_mod.time() < _RATE_LIMIT_UNTIL:
        wait = _RATE_LIMIT_UNTIL - _time_mod.time()
        logger.info(f"CoinGecko rate limit cooldown: {wait:.0f}s remaining — using cache")
        return None
    try:
        resp = requests.get(url, params=params, timeout=timeout, verify=certifi.where())
        if resp.status_code == 429:
            # Back off 60 seconds on rate limit
            _RATE_LIMIT_UNTIL = _time_mod.time() + 60
            logger.warning("CoinGecko rate limited (429) — pausing calls for 60s")
            return None
        resp.raise_for_status()
        return resp
    except requests.exceptions.HTTPError as e:
        if "429" in str(e):
            _RATE_LIMIT_UNTIL = _time_mod.time() + 60
            logger.warning("CoinGecko rate limited — pausing 60s")
        else:
            logger.error(f"CoinGecko request failed: {e}")
        return None
    except Exception as e:
        logger.error(f"CoinGecko request failed: {e}")
        return None

COINGECKO_IDS = {
    "WETH":  "ethereum",
    "USDC":  "usd-coin",
    "USDT":  "tether",
    "DAI":   "dai",
    "cbBTC": "coinbase-wrapped-btc",
    "cbETH": "coinbase-wrapped-staked-eth",
    "VVV":   "venice-token",
    "AERO":  "aerodrome-finance",
}

COINGECKO_MARKETS  = "https://api.coingecko.com/api/v3/coins/markets"
FEAR_GREED_URL     = "https://api.alternative.me/fng/"
COINGECKO_TRENDING = "https://api.coingecko.com/api/v3/search/trending"

SSL = certifi.where()

import time as _time

_MARKET_CACHE: dict = {}
_MARKET_CACHE_TS: float = 0
_CACHE_TTL: int = 90  # seconds — reuse data within same tick


class Market:
    def __init__(self, w3=None):
        self.w3 = w3

    def get_market_data(self) -> dict[str, dict]:
        global _MARKET_CACHE, _MARKET_CACHE_TS
        if _MARKET_CACHE and (_time.time() - _MARKET_CACHE_TS) < _CACHE_TTL:
            return _MARKET_CACHE
        """
        Fetch prices + 1h/24h change % + volume for all whitelisted tokens.
        Uses CoinGecko /coins/markets which returns rich data in one call.
        """
        non_stable = {k: v for k, v in COINGECKO_IDS.items() if k not in STABLECOINS}
        ids = ",".join(non_stable.values())
        try:
            resp = _cg_get(
                COINGECKO_MARKETS,
                params={
                    "vs_currency": "usd",
                    "ids": ids,
                    "price_change_percentage": "1h,24h",
                    "per_page": 20,
                },
            )
            if resp is None:
                return _MARKET_CACHE if _MARKET_CACHE else {}
            raw = {coin["id"]: coin for coin in resp.json()}

            result = {}
            id_to_sym = {v: k for k, v in COINGECKO_IDS.items()}

            for cg_id, coin in raw.items():
                sym = id_to_sym.get(cg_id)
                if not sym:
                    continue
                result[sym] = {
                    "price":        coin.get("current_price", 0.0),
                    "change_1h":    coin.get("price_change_percentage_1h_in_currency", 0.0),
                    "change_24h":   coin.get("price_change_percentage_24h", 0.0),
                    "volume_24h":   coin.get("total_volume", 0.0),
                    "market_cap":   coin.get("market_cap", 0.0),
                    "high_24h":     coin.get("high_24h", 0.0),
                    "low_24h":      coin.get("low_24h", 0.0),
                }

            for sym in STABLECOINS:
                result[sym] = {
                    "price": 1.0, "change_1h": 0.0, "change_24h": 0.0,
                    "volume_24h": 0.0, "market_cap": 0.0,
                    "high_24h": 1.0, "low_24h": 1.0,
                }

            for sym, d in result.items():
                if sym not in STABLECOINS:
                    logger.info(
                        f"{sym}: ${d['price']:,.2f} | "
                        f"1h: {d['change_1h']:+.2f}% | "
                        f"24h: {d['change_24h']:+.2f}% | "
                        f"vol: ${d['volume_24h']:,.0f}"
                    )
            _MARKET_CACHE    = result
            _MARKET_CACHE_TS = _time.time()
            return result

        except Exception as e:
            logger.error(f"Market data fetch failed: {e}")
            return _MARKET_CACHE if _MARKET_CACHE else {}

    def get_all_prices(self) -> dict[str, float]:
        data = self.get_market_data()
        return {sym: d["price"] for sym, d in data.items()} if data else {sym: 0.0 for sym in TOKENS}

    def get_price_usd(self, symbol: str) -> float:
        return self.get_all_prices().get(symbol.upper(), 0.0)

    def get_fear_and_greed(self) -> dict:
        try:
            resp = requests.get(FEAR_GREED_URL, timeout=10, verify=SSL)
            resp.raise_for_status()
            data = resp.json()["data"][0]
            result = {"value": int(data["value"]), "label": data["value_classification"]}
            logger.info(f"Fear & Greed: {result['value']} ({result['label']})")
            return result
        except Exception as e:
            logger.warning(f"Fear & Greed fetch failed: {e}")
            return {"value": 50, "label": "Unknown"}

    def get_trending_tokens(self) -> list[dict]:
        try:
            resp = _cg_get(COINGECKO_TRENDING, params={})
            if resp is None:
                return []
            coins = resp.json().get("coins", [])
            trending = []
            for item in coins[:7]:
                coin = item["item"]
                trending.append({
                    "name": coin["name"],
                    "symbol": coin["symbol"].upper(),
                    "market_cap_rank": coin.get("market_cap_rank"),
                })
            logger.info(f"Trending: {[t['symbol'] for t in trending]}")
            return trending
        except Exception as e:
            logger.warning(f"Trending fetch failed: {e}")
            return []

    def get_aerodrome_top_pools(self) -> list[dict]:
        # Aerodrome public API not currently available — using DeFiLlama for Base DEX data instead
        return []

    def get_full_context(self) -> dict:
        market_data = self.get_market_data()
        return {
            "prices":          {sym: d["price"] for sym, d in market_data.items()},
            "market_data":     market_data,
            "fear_and_greed":  self.get_fear_and_greed(),
            "trending_tokens": self.get_trending_tokens(),
            "aerodrome_pools": self.get_aerodrome_top_pools(),
        }
