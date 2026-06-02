import json
import math
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


def calculate_ema(prices: list[float], period: int) -> list[float] | None:
    if len(prices) < period:
        return None
    k = 2 / (period + 1)
    ema = [sum(prices[:period]) / period]
    for price in prices[period:]:
        ema.append(price * k + ema[-1] * (1 - k))
    return ema


def calculate_macd(prices: list[float]) -> dict | None:
    """MACD (12/26/9 EMA). Returns macd_line, signal, histogram."""
    if len(prices) < 35:
        return None
    ema12 = calculate_ema(prices, 12)
    ema26 = calculate_ema(prices, 26)
    if not ema12 or not ema26:
        return None
    # Align: ema26 is shorter, trim ema12 to match
    diff = len(ema12) - len(ema26)
    ema12 = ema12[diff:]
    macd_line = [a - b for a, b in zip(ema12, ema26)]
    if len(macd_line) < 9:
        return None
    signal_ema = calculate_ema(macd_line, 9)
    if not signal_ema:
        return None
    macd_val   = round(macd_line[-1], 6)
    signal_val = round(signal_ema[-1], 6)
    return {
        "macd":      macd_val,
        "signal":    signal_val,
        "histogram": round(macd_val - signal_val, 6),
        "crossover": "bullish" if macd_val > signal_val else "bearish",
    }


def calculate_bollinger_bands(prices: list[float], period: int = 20) -> dict | None:
    """Bollinger Bands (20-period, 2 std dev)."""
    if len(prices) < period:
        return None
    window = prices[-period:]
    mid = sum(window) / period
    variance = sum((p - mid) ** 2 for p in window) / period
    std = math.sqrt(variance)
    upper = round(mid + 2 * std, 6)
    lower = round(mid - 2 * std, 6)
    mid   = round(mid, 6)
    current = prices[-1]
    width_pct = round((upper - lower) / mid * 100, 2) if mid else None
    # Where is current price within the bands (0=lower, 1=upper)
    position = round((current - lower) / (upper - lower), 3) if (upper - lower) > 0 else None
    squeeze = width_pct is not None and width_pct < 5  # narrow bands = squeeze
    return {
        "upper":     upper,
        "mid":       mid,
        "lower":     lower,
        "width_pct": width_pct,
        "position":  position,   # 0.0=at lower, 1.0=at upper
        "squeeze":   squeeze,    # True = volatility breakout likely soon
    }


def get_support_resistance(prices: list[float], lookback: int = 20) -> dict | None:
    """Recent support and resistance from price highs/lows."""
    if len(prices) < 5:
        return None
    window = prices[-lookback:] if len(prices) >= lookback else prices
    resistance = round(max(window), 6)
    support    = round(min(window), 6)
    current    = prices[-1]
    dist_to_resistance = round((resistance - current) / current * 100, 2) if current else None
    dist_to_support    = round((current - support) / current * 100, 2) if current else None
    return {
        "resistance":          resistance,
        "support":             support,
        "dist_to_resistance_pct": dist_to_resistance,
        "dist_to_support_pct":    dist_to_support,
    }


def get_indicators(symbol: str) -> dict:
    """Return all technical indicators for a symbol."""
    prices = get_prices(symbol)
    n = len(prices)

    rsi    = calculate_rsi(prices)
    sma5   = calculate_sma(prices, 5)
    sma20  = calculate_sma(prices, 20)
    macd   = calculate_macd(prices)
    bb     = calculate_bollinger_bands(prices)
    sr     = get_support_resistance(prices)

    trend = None
    if sma5 and sma20:
        trend = "bullish" if sma5 > sma20 else "bearish"

    momentum_1h = None
    if n >= 2:
        momentum_1h = round((prices[-1] - prices[-2]) / prices[-2] * 100, 2)

    momentum_4h = None
    if n >= 8:
        momentum_4h = round((prices[-1] - prices[-8]) / prices[-8] * 100, 2)

    momentum_24h = None
    if n >= 48:
        momentum_24h = round((prices[-1] - prices[-48]) / prices[-48] * 100, 2)

    return {
        "data_points":    n,
        "rsi_14":         rsi,
        "sma_5":          sma5,
        "sma_20":         sma20,
        "trend":          trend,
        "momentum_1h_pct":  momentum_1h,
        "momentum_4h_pct":  momentum_4h,
        "momentum_24h_pct": momentum_24h,
        "macd":             macd,
        "bollinger_bands":  bb,
        "support_resistance": sr,
        "current_price":    prices[-1] if prices else None,
    }


def get_all_indicators(symbols: list[str]) -> dict[str, dict]:
    return {sym: get_indicators(sym) for sym in symbols}
