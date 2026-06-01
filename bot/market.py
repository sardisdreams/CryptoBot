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

COINGECKO_URL       = "https://api.coingecko.com/api/v3/simple/price"
FEAR_GREED_URL      = "https://api.alternative.me/fng/"
COINGECKO_TRENDING  = "https://api.coingecko.com/api/v3/search/trending"

SSL = certifi.where()


class Market:
    def __init__(self, w3=None):
        self.w3 = w3

    def get_all_prices(self) -> dict[str, float]:
        ids = ",".join(COINGECKO_IDS.values())
        try:
            response = requests.get(
                COINGECKO_URL,
                params={"ids": ids, "vs_currencies": "usd"},
                timeout=10,
                verify=SSL,
            )
            response.raise_for_status()
            data = response.json()

            prices = {}
            for symbol in TOKENS:
                if symbol in STABLECOINS:
                    prices[symbol] = 1.0
                    continue
                cg_id = COINGECKO_IDS.get(symbol)
                if cg_id and cg_id in data:
                    prices[symbol] = data[cg_id]["usd"]
                else:
                    prices[symbol] = 0.0
                    logger.warning(f"No price data for {symbol}")

            logger.info(f"Prices: { {k: f'${v:,.2f}' for k, v in prices.items()} }")
            return prices

        except Exception as e:
            logger.error(f"Price fetch failed: {e}")
            return {symbol: 0.0 for symbol in TOKENS}

    def get_price_usd(self, symbol: str) -> float:
        return self.get_all_prices().get(symbol.upper(), 0.0)

    def get_fear_and_greed(self) -> dict:
        """
        Returns the Crypto Fear & Greed Index.
        Value 0-100: 0=Extreme Fear, 50=Neutral, 100=Extreme Greed.
        """
        try:
            response = requests.get(FEAR_GREED_URL, timeout=10, verify=SSL)
            response.raise_for_status()
            data = response.json()["data"][0]
            result = {
                "value": int(data["value"]),
                "label": data["value_classification"],  # e.g. "Fear", "Greed", "Extreme Greed"
            }
            logger.info(f"Fear & Greed: {result['value']} ({result['label']})")
            return result
        except Exception as e:
            logger.warning(f"Fear & Greed fetch failed: {e}")
            return {"value": 50, "label": "Unknown"}

    def get_trending_tokens(self) -> list[dict]:
        """
        Returns top trending tokens on CoinGecko.
        Useful for detecting hype / potential pump-and-dump targets to AVOID.
        """
        try:
            response = requests.get(COINGECKO_TRENDING, timeout=10, verify=SSL)
            response.raise_for_status()
            coins = response.json().get("coins", [])
            trending = []
            for item in coins[:7]:
                coin = item["item"]
                trending.append({
                    "name": coin["name"],
                    "symbol": coin["symbol"].upper(),
                    "market_cap_rank": coin.get("market_cap_rank"),
                    "score": coin.get("score", 0),
                })
            logger.info(f"Trending: {[t['symbol'] for t in trending]}")
            return trending
        except Exception as e:
            logger.warning(f"Trending fetch failed: {e}")
            return []

    def get_full_context(self) -> dict:
        """Single call that returns prices + fear/greed + trending for the agent."""
        return {
            "prices": self.get_all_prices(),
            "fear_and_greed": self.get_fear_and_greed(),
            "trending_tokens": self.get_trending_tokens(),
        }
