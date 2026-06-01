import requests
import certifi
from bot.config import TOKENS, STABLECOINS
from bot.logger import setup_logger

logger = setup_logger("market")

COINGECKO_IDS = {
    "WETH":  "ethereum",
    "USDC":  "usd-coin",
    "USDT":  "tether",
    "DAI":   "dai",
    "cbBTC": "coinbase-wrapped-btc",
    "cbETH": "coinbase-wrapped-staked-eth",
}

COINGECKO_MARKETS  = "https://api.coingecko.com/api/v3/coins/markets"
FEAR_GREED_URL     = "https://api.alternative.me/fng/"
COINGECKO_TRENDING = "https://api.coingecko.com/api/v3/search/trending"

SSL = certifi.where()


class Market:
    def __init__(self, w3=None):
        self.w3 = w3

    def get_market_data(self) -> dict[str, dict]:
        """
        Fetch prices + 1h/24h change % + volume for all whitelisted tokens.
        Uses CoinGecko /coins/markets which returns rich data in one call.
        """
        non_stable = {k: v for k, v in COINGECKO_IDS.items() if k not in STABLECOINS}
        ids = ",".join(non_stable.values())
        try:
            resp = requests.get(
                COINGECKO_MARKETS,
                params={
                    "vs_currency": "usd",
                    "ids": ids,
                    "price_change_percentage": "1h,24h",
                    "per_page": 20,
                },
                timeout=10,
                verify=SSL,
            )
            resp.raise_for_status()
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
            return result

        except Exception as e:
            logger.error(f"Market data fetch failed: {e}")
            return {}

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
            resp = requests.get(COINGECKO_TRENDING, timeout=10, verify=SSL)
            resp.raise_for_status()
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
        """
        Fetch top liquidity pools from Aerodrome via their public API.
        Shows which tokens have real trading activity on Base.
        """
        try:
            resp = requests.get(
                "https://api.aerodrome.finance/api/v1/pools",
                params={"limit": 20, "sort": "volume"},
                timeout=10,
                verify=SSL,
            )
            resp.raise_for_status()
            pools = resp.json().get("data", []) or resp.json()
            result = []
            for p in pools[:10]:
                token0 = p.get("token0", {}).get("symbol", "?")
                token1 = p.get("token1", {}).get("symbol", "?")
                vol = float(p.get("volumeUSD", 0) or p.get("volume24h", 0) or 0)
                result.append({"pair": f"{token0}/{token1}", "volume_usd": vol})
            logger.info(f"Aerodrome top pools: {[p['pair'] for p in result[:5]]}")
            return result
        except Exception as e:
            logger.warning(f"Aerodrome pools fetch failed: {e}")
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
