"""
4h OHLCV cache — fetches 4h candle data from CoinGecko for signal calculations.
Refreshed every 1 hour per token; stale cache returned on API failure.
days=14 → 4h granularity on CoinGecko (6 candles/day × 14 days ≈ 84 candles).
"""
import json
import os
import time
import certifi
import requests
from datetime import datetime, timezone

OHLCV_FILE          = "data/ohlcv_cache.json"
CACHE_MAX_AGE_HOURS = 1
_RATE_LIMIT_SLEEP   = 1.2   # seconds between CoinGecko calls


def get_candles(cg_id: str, days: int = 14) -> list[dict]:
    """
    Return 4h OHLCV candles for cg_id, oldest first.
    Uses cache if < 1h old; returns stale cache on fetch failure.
    days=14 → 4h granularity on CoinGecko (≈84 candles, enough for EMA50).
    """
    cache = _load()
    entry = cache.get(cg_id, {})

    if _is_fresh(entry.get("cached_at")):
        return entry.get("candles", [])

    time.sleep(_RATE_LIMIT_SLEEP)
    candles = _fetch(cg_id, days)
    if candles:
        cache[cg_id] = {
            "candles":   candles,
            "cached_at": datetime.now(timezone.utc).isoformat(),
        }
        _save(cache)
        return candles

    return entry.get("candles", [])


def _fetch(cg_id: str, days: int) -> list[dict]:
    try:
        resp = requests.get(
            f"https://api.coingecko.com/api/v3/coins/{cg_id}/ohlc",
            params={"vs_currency": "usd", "days": days},
            timeout=15,
            verify=certifi.where(),
        )
        if resp.status_code == 200:
            raw = resp.json()
            return [
                {
                    "ts":    datetime.fromtimestamp(row[0] / 1000, tz=timezone.utc).isoformat(),
                    "open":  row[1],
                    "high":  row[2],
                    "low":   row[3],
                    "close": row[4],
                }
                for row in raw
                if len(row) == 5
            ]
    except Exception:
        pass
    return []


def _is_fresh(cached_at: str | None) -> bool:
    if not cached_at:
        return False
    try:
        ts = datetime.fromisoformat(cached_at)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds() / 3600 < CACHE_MAX_AGE_HOURS
    except Exception:
        return False


def _load() -> dict:
    if not os.path.exists(OHLCV_FILE):
        return {}
    try:
        with open(OHLCV_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save(data: dict):
    os.makedirs("data", exist_ok=True)
    tmp = OHLCV_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, OHLCV_FILE)
