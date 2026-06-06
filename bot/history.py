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


def calculate_adx(prices: list[float], period: int = 14) -> dict | None:
    """
    Average Directional Index — measures trend STRENGTH (not direction).
    ADX > 25: trending market (use trend-following signals).
    ADX < 20: ranging market (use mean-reversion signals).
    """
    if len(prices) < period * 2 + 1:
        return None
    # Compute True Range and Directional Movement
    tr_list, dm_plus, dm_minus = [], [], []
    for i in range(1, len(prices)):
        high = prices[i]
        low  = prices[i]
        prev = prices[i - 1]
        # Approximate: no OHLCV so use price change as proxy
        tr   = abs(high - prev)
        up   = high - prev
        down = prev - low
        tr_list.append(tr)
        dm_plus.append(up if up > down and up > 0 else 0)
        dm_minus.append(down if down > up and down > 0 else 0)

    def smooth(lst, p):
        s = sum(lst[:p])
        result = [s]
        for v in lst[p:]:
            s = s - s / p + v
            result.append(s)
        return result

    atr14    = smooth(tr_list, period)
    dmp14    = smooth(dm_plus, period)
    dmm14    = smooth(dm_minus, period)

    di_plus  = [100 * dmp14[i] / atr14[i] if atr14[i] else 0 for i in range(len(atr14))]
    di_minus = [100 * dmm14[i] / atr14[i] if atr14[i] else 0 for i in range(len(atr14))]
    dx       = [100 * abs(di_plus[i] - di_minus[i]) / (di_plus[i] + di_minus[i])
                if (di_plus[i] + di_minus[i]) > 0 else 0
                for i in range(len(di_plus))]

    if len(dx) < period:
        return None
    adx = sum(dx[-period:]) / period
    latest_dip = di_plus[-1]
    latest_dim = di_minus[-1]
    return {
        "adx":      round(adx, 2),
        "di_plus":  round(latest_dip, 2),
        "di_minus": round(latest_dim, 2),
        "trending": adx > 25,
        "regime":   "trending" if adx > 25 else "ranging",
        "direction": "up" if latest_dip > latest_dim else "down",
    }


def calculate_obv(prices: list[float]) -> dict | None:
    """
    On-Balance Volume — volume pressure indicator.
    Rising OBV with flat/falling price = accumulation (bullish).
    Falling OBV with flat/rising price = distribution (bearish).
    We approximate volume as price change magnitude (no real volume data from CoinGecko history).
    """
    if len(prices) < 10:
        return None
    obv = [0.0]
    for i in range(1, len(prices)):
        change = prices[i] - prices[i - 1]
        vol_proxy = abs(change)  # price change magnitude as volume proxy
        if change > 0:
            obv.append(obv[-1] + vol_proxy)
        elif change < 0:
            obv.append(obv[-1] - vol_proxy)
        else:
            obv.append(obv[-1])

    # OBV trend: compare current OBV to 10-period average
    obv_sma = sum(obv[-10:]) / 10
    current_obv = obv[-1]
    price_trend = "up" if prices[-1] > prices[-10] else "down"
    obv_trend   = "up" if current_obv > obv_sma else "down"

    # Divergence detection
    divergence = None
    if price_trend == "up" and obv_trend == "down":
        divergence = "bearish"  # price rising but volume declining — reversal warning
    elif price_trend == "down" and obv_trend == "up":
        divergence = "bullish"  # price falling but volume accumulating — reversal signal

    return {
        "obv":        round(current_obv, 6),
        "obv_trend":  obv_trend,
        "divergence": divergence,
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
    adx    = calculate_adx(prices)
    obv    = calculate_obv(prices)

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
        "adx":              adx,
        "obv":              obv,
        "current_price":    prices[-1] if prices else None,
    }


def get_all_indicators(symbols: list[str]) -> dict[str, dict]:
    return {sym: get_indicators(sym) for sym in symbols}


def get_market_regime(btc_indicators: dict, fear_greed_value: int) -> dict:
    """
    Classify current market regime using BTC technicals and Fear & Greed.
    Returns regime label and trading guidance.
    """
    btc_1h   = btc_indicators.get("momentum_1h_pct") or 0
    btc_4h   = btc_indicators.get("momentum_4h_pct") or 0
    btc_24h  = btc_indicators.get("momentum_24h_pct") or 0
    btc_trend = btc_indicators.get("trend", "unknown")
    btc_rsi  = btc_indicators.get("rsi_14") or 50

    # Score: positive = bullish, negative = bearish
    score = 0
    if btc_1h > 1:   score += 1
    if btc_1h < -1:  score -= 1
    if btc_4h > 2:   score += 2
    if btc_4h < -2:  score -= 2
    if btc_24h > 3:  score += 2
    if btc_24h < -3: score -= 2
    if btc_trend == "bullish": score += 1
    if btc_trend == "bearish": score -= 1
    if fear_greed_value >= 60: score += 1
    if fear_greed_value <= 25: score -= 1

    if score >= 4:
        regime = "STRONG_BULL"
        guidance = "Aggressive entries OK. All 5 setups valid. Size up on high-conviction signals."
    elif score >= 2:
        regime = "BULL"
        guidance = "Favor longs. All setups valid. Standard sizing."
    elif score >= -1:
        regime = "SIDEWAYS"
        guidance = "Selective entries only. Prefer Setup 5 (oversold) and Setup 2 (clear dip recovery). Tighter position sizes."
    elif score >= -3:
        regime = "BEAR"
        guidance = "Caution. Only Setup 5 with confirmed 1h reversal. Reduce size 50%. Hold more USDC."
    else:
        regime = "STRONG_BEAR"
        guidance = "Defensive. No new entries unless RSI extremely oversold with strong reversal confirmation. Preserve capital."

    return {
        "regime":    regime,
        "score":     score,
        "guidance":  guidance,
        "btc_1h":    btc_1h,
        "btc_4h":    btc_4h,
        "btc_24h":   btc_24h,
        "fear_greed": fear_greed_value,
    }


def get_session_context() -> dict:
    """
    Return current trading session based on UTC hour.
    Crypto volume follows global market hours.
    """
    from datetime import datetime, timezone
    hour = datetime.now(timezone.utc).hour

    if 0 <= hour < 7:
        session = "ASIA_NIGHT"
        volume_note = "Low volume. Thin order books. Signals are noisier — require stronger confirmation before entry."
        aggressive = False
    elif 7 <= hour < 9:
        session = "EUROPE_OPEN"
        volume_note = "Volume picking up. European markets opening. Watch for morning momentum setups."
        aggressive = True
    elif 9 <= hour < 13:
        session = "EUROPE_PEAK"
        volume_note = "Good volume. Reliable signals. All setups valid."
        aggressive = True
    elif 13 <= hour < 17:
        session = "US_OPEN"
        volume_note = "Highest volume of the day. US market open drives crypto. Strong momentum setups most reliable."
        aggressive = True
    elif 17 <= hour < 21:
        session = "US_AFTERNOON"
        volume_note = "Good volume, slight afternoon drift. All setups valid."
        aggressive = True
    else:
        session = "US_NIGHT"
        volume_note = "Volume declining. Be selective. Favor high-conviction setups only."
        aggressive = False

    return {
        "session":     session,
        "hour_utc":    hour,
        "volume_note": volume_note,
        "aggressive":  aggressive,
    }
