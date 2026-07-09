"""
Hyperliquid market data — candles, prices, funding rates, meta.
All data comes from Hyperliquid's own REST API (no CoinGecko dependency).
"""
import time
import requests
from bot.logger import setup_logger
from hyperliquid.config import HL_API_URL, COINS, HL_CANDLE_INTERVAL, HL_CANDLE_LOOKBACK

logger = setup_logger("hl.market")

_SESSION = requests.Session()
_SESSION.headers.update({"Content-Type": "application/json"})


def _post(payload: dict, timeout: int = 10) -> dict:
    resp = _SESSION.post(f"{HL_API_URL}/info", json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def get_candles(coin: str, interval: str = HL_CANDLE_INTERVAL, lookback: int = HL_CANDLE_LOOKBACK) -> list[dict]:
    """
    Fetch OHLCV candles for a coin from Hyperliquid.
    Returns list of dicts: {t, o, h, l, c, v} with float values, sorted oldest→newest.
    """
    interval_ms = _interval_to_ms(interval)
    end_ts   = int(time.time() * 1000)
    start_ts = end_ts - (lookback * interval_ms)

    try:
        raw = _post({
            "type": "candleSnapshot",
            "req": {"coin": coin, "interval": interval, "startTime": start_ts, "endTime": end_ts}
        })
        candles = []
        for c in raw:
            candles.append({
                "t": int(c["t"]),
                "o": float(c["o"]),
                "h": float(c["h"]),
                "l": float(c["l"]),
                "c": float(c["c"]),
                "v": float(c["v"]),
            })
        return sorted(candles, key=lambda x: x["t"])
    except Exception as e:
        logger.warning(f"get_candles({coin}): {e}")
        return []


def get_all_prices() -> dict[str, float]:
    """Return {coin: mid_price} for all configured coins."""
    try:
        raw = _post({"type": "allMids"})
        prices = {}
        for coin in COINS:
            val = raw.get(coin)
            if val:
                try:
                    prices[coin] = float(val)
                except (ValueError, TypeError):
                    pass
        return prices
    except Exception as e:
        logger.warning(f"get_all_prices: {e}")
        return {}


def get_funding_rates() -> dict[str, float]:
    """
    Return current funding rate per 8h for each coin.
    Positive = longs pay shorts. Negative = shorts pay longs.
    Used to avoid entering a position when funding is strongly against us.
    """
    try:
        raw = _post({"type": "metaAndAssetCtxs"})
        meta   = raw[0].get("universe", [])
        ctxs   = raw[1] if len(raw) > 1 else []
        rates  = {}
        for i, asset in enumerate(meta):
            name = asset.get("name", "")
            if name in COINS and i < len(ctxs):
                try:
                    rates[name] = float(ctxs[i].get("funding", 0))
                except (ValueError, TypeError):
                    rates[name] = 0.0
        return rates
    except Exception as e:
        logger.warning(f"get_funding_rates: {e}")
        return {}


def get_user_state(address: str) -> dict:
    """Return full user account state from Hyperliquid."""
    try:
        return _post({"type": "clearinghouseState", "user": address})
    except Exception as e:
        logger.warning(f"get_user_state: {e}")
        return {}


def get_open_perp_positions(address: str) -> list[dict]:
    """
    Return list of open perp positions for the wallet.
    Each dict: {coin, size, side, entry_price, unrealized_pnl, liquidation_px}
    size > 0 = long, size < 0 = short.
    """
    state = get_user_state(address)
    positions = []
    for item in state.get("assetPositions", []):
        pos = item.get("position", {})
        szi = float(pos.get("szi", 0))
        if szi == 0:
            continue
        positions.append({
            "coin":            pos.get("coin", ""),
            "size":            szi,
            "side":            "long" if szi > 0 else "short",
            "entry_price":     float(pos.get("entryPx") or 0),
            "unrealized_pnl":  float(pos.get("unrealizedPnl") or 0),
            "liquidation_px":  float(pos.get("liquidationPx") or 0),
            "leverage":        pos.get("leverage", {}).get("value", 1),
        })
    return positions


def get_margin_summary(address: str) -> dict:
    """Return account value and available margin."""
    state = get_user_state(address)
    summary = state.get("crossMarginSummary", {})
    return {
        "account_value":     float(summary.get("accountValue", 0)),
        "total_margin_used": float(summary.get("totalMarginUsed", 0)),
        "total_raw_usd":     float(summary.get("totalRawUsd", 0)),
    }


def _interval_to_ms(interval: str) -> int:
    mapping = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000,
               "4h": 14_400_000, "1d": 86_400_000}
    return mapping.get(interval, 3_600_000)
