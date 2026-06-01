import json
import os
from datetime import datetime, timezone

HISTORY_FILE = "data/price_history.json"
MAX_HISTORY = 50  # keep last 50 data points (~25 hours at 30min intervals)


def _load() -> dict:
    if not os.path.exists(HISTORY_FILE):
        return {}
    with open(HISTORY_FILE, "r") as f:
        return json.load(f)


def _save(data: dict):
    os.makedirs("data", exist_ok=True)
    with open(HISTORY_FILE, "w") as f:
        json.dump(data, f)


def record_prices(prices: dict[str, float]):
    """Append current prices to history file."""
    history = _load()
    ts = datetime.now(timezone.utc).isoformat()
    for symbol, price in prices.items():
        if price <= 0:
            continue
        if symbol not in history:
            history[symbol] = []
        history[symbol].append({"ts": ts, "price": price})
        history[symbol] = history[symbol][-MAX_HISTORY:]
    _save(history)


def get_prices(symbol: str) -> list[float]:
    """Return list of historical prices for a symbol, oldest first."""
    history = _load()
    return [entry["price"] for entry in history.get(symbol, [])]


def calculate_rsi(prices: list[float], period: int = 14) -> float | None:
    if len(prices) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(prices)):
        delta = prices[i] - prices[i - 1]
        gains.append(max(delta, 0))
        losses.append(max(-delta, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def calculate_sma(prices: list[float], period: int) -> float | None:
    if len(prices) < period:
        return None
    return round(sum(prices[-period:]) / period, 4)


def get_indicators(symbol: str) -> dict:
    """Return all technical indicators for a symbol."""
    prices = get_prices(symbol)
    n = len(prices)

    rsi = calculate_rsi(prices)
    sma5 = calculate_sma(prices, 5)
    sma20 = calculate_sma(prices, 20)

    trend = None
    if sma5 and sma20:
        trend = "bullish" if sma5 > sma20 else "bearish"

    momentum_1h = None
    if n >= 2:
        momentum_1h = round((prices[-1] - prices[-2]) / prices[-2] * 100, 2)

    momentum_4h = None
    if n >= 8:
        momentum_4h = round((prices[-1] - prices[-8]) / prices[-8] * 100, 2)

    return {
        "data_points": n,
        "rsi_14": rsi,
        "sma_5": sma5,
        "sma_20": sma20,
        "trend": trend,
        "momentum_1h_pct": momentum_1h,
        "momentum_4h_pct": momentum_4h,
        "current_price": prices[-1] if prices else None,
    }


def get_all_indicators(symbols: list[str]) -> dict[str, dict]:
    return {sym: get_indicators(sym) for sym in symbols}
