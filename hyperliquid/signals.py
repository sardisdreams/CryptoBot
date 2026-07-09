"""
Signal engine for Hyperliquid perpetuals.

Scores each coin 0-100 and assigns direction (LONG / SHORT / NONE).
Uses 1h candles and classic trend-following indicators.

Design philosophy:
- Long in uptrends, short in downtrends — never fight the trend
- RSI guards against overbought longs and oversold shorts
- Multiple confirming conditions required before acting
- Score ≥ 65 = auto-execute. Score 50-64 = Claude review.
"""
import numpy as np
from bot.logger import setup_logger
from hyperliquid import market
from hyperliquid.config import COINS, SIGNAL_MIN_ENTRY

logger = setup_logger("hl.signals")


# ── Indicator calculations ────────────────────────────────────────────────────

def _ema(values: np.ndarray, period: int) -> np.ndarray:
    k = 2 / (period + 1)
    result = np.zeros_like(values, dtype=float)
    result[0] = values[0]
    for i in range(1, len(values)):
        result[i] = values[i] * k + result[i - 1] * (1 - k)
    return result


def _rsi(closes: np.ndarray, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    deltas = np.diff(closes[-(period + 1):])
    gains  = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = gains.mean()
    avg_loss = losses.mean()
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _macd(closes: np.ndarray) -> tuple[float, float]:
    """Return (macd_line, signal_line) using 12/26/9 settings."""
    if len(closes) < 35:
        return 0.0, 0.0
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    macd_line = ema12 - ema26
    signal    = _ema(macd_line, 9)
    return float(macd_line[-1]), float(signal[-1])


def _atr(candles: list[dict], period: int = 14) -> float:
    if len(candles) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        h = candles[i]["h"]
        l = candles[i]["l"]
        pc = candles[i - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return float(np.mean(trs[-period:]))


# ── Scoring ───────────────────────────────────────────────────────────────────

def score_coin(coin: str, candles: list[dict]) -> dict:
    """
    Score a coin and determine trade direction.

    Returns:
        {
          "coin": str,
          "score": int (0-100),
          "direction": "long" | "short" | "none",
          "entry_ok": bool,
          "signals": dict,   # individual flag values for logging/Claude
          "price": float,
        }
    """
    if len(candles) < 60:
        return _no_signal(coin, "insufficient candle history")

    closes  = np.array([c["c"] for c in candles], dtype=float)
    price   = closes[-1]

    ema20   = _ema(closes, 20)[-1]
    ema50   = _ema(closes, 50)[-1]
    rsi     = _rsi(closes)
    macd, macd_sig = _macd(closes)
    atr     = _atr(candles)
    atr_pct = (atr / price * 100) if price > 0 else 0

    # Recent high/low for "chasing" detection
    recent_high = max(c["h"] for c in candles[-20:])
    recent_low  = min(c["l"] for c in candles[-20:])
    pct_from_high = (recent_high - price) / recent_high * 100 if recent_high > 0 else 0
    pct_from_low  = (price - recent_low) / price * 100 if price > 0 else 0

    # 1h momentum (latest candle change)
    mom_1h = ((closes[-1] - closes[-2]) / closes[-2] * 100) if len(closes) >= 2 else 0

    # ── Long scoring ──────────────────────────────────────────────────────────
    long_score = 0
    long_flags = {}

    # Trend: price above EMA20 and EMA20 above EMA50
    if price > ema20:
        long_score += 20
        long_flags["above_ema20"] = True
    else:
        long_flags["above_ema20"] = False

    if ema20 > ema50:
        long_score += 15
        long_flags["ema20_above_ema50"] = True
    else:
        long_flags["ema20_above_ema50"] = False

    # RSI: ideal long entry is 35-60 (not overbought, not collapsed)
    if 35 <= rsi <= 60:
        long_score += 25
        long_flags["rsi_long_ok"] = True
    elif 25 <= rsi < 35:
        long_score += 10   # oversold bounce possible but risky
        long_flags["rsi_long_ok"] = "borderline_oversold"
    else:
        long_flags["rsi_long_ok"] = False

    # MACD bullish (macd line above signal)
    if macd > macd_sig:
        long_score += 20
        long_flags["macd_bullish"] = True
    else:
        long_flags["macd_bullish"] = False

    # 1h momentum positive
    if mom_1h > 0.1:
        long_score += 10
        long_flags["momentum_1h"] = True
    else:
        long_flags["momentum_1h"] = False

    # Not chasing: at least 2% below recent high
    if pct_from_high >= 2.0:
        long_score += 10
        long_flags["not_chasing"] = True
    else:
        long_score -= 10
        long_flags["not_chasing"] = False

    # ATR sanity: not so volatile we can't set tight stops
    if 1.0 <= atr_pct <= 8.0:
        long_flags["atr_ok"] = True
    else:
        long_score -= 10
        long_flags["atr_ok"] = False

    # ── Short scoring ─────────────────────────────────────────────────────────
    short_score = 0
    short_flags = {}

    if price < ema20:
        short_score += 20
        short_flags["below_ema20"] = True
    else:
        short_flags["below_ema20"] = False

    if ema20 < ema50:
        short_score += 15
        short_flags["ema20_below_ema50"] = True
    else:
        short_flags["ema20_below_ema50"] = False

    if 40 <= rsi <= 65:
        short_score += 25
        short_flags["rsi_short_ok"] = True
    elif 65 < rsi <= 75:
        short_score += 10
        short_flags["rsi_short_ok"] = "borderline_overbought"
    else:
        short_flags["rsi_short_ok"] = False

    if macd < macd_sig:
        short_score += 20
        short_flags["macd_bearish"] = True
    else:
        short_flags["macd_bearish"] = False

    if mom_1h < -0.1:
        short_score += 10
        short_flags["momentum_1h_neg"] = True
    else:
        short_flags["momentum_1h_neg"] = False

    if pct_from_low >= 2.0:
        short_score += 10
        short_flags["not_chasing_low"] = True
    else:
        short_score -= 10
        short_flags["not_chasing_low"] = False

    if 1.0 <= atr_pct <= 8.0:
        short_flags["atr_ok"] = True
    else:
        short_score -= 10
        short_flags["atr_ok"] = False

    # ── Direction decision ────────────────────────────────────────────────────
    long_score  = max(0, min(100, long_score))
    short_score = max(0, min(100, short_score))

    if long_score >= short_score and long_score >= SIGNAL_MIN_ENTRY:
        direction = "long"
        score     = long_score
        flags     = long_flags
    elif short_score > long_score and short_score >= SIGNAL_MIN_ENTRY:
        direction = "short"
        score     = short_score
        flags     = short_flags
    else:
        direction = "none"
        score     = max(long_score, short_score)
        flags     = {**long_flags, **short_flags}

    result = {
        "coin":      coin,
        "score":     score,
        "direction": direction,
        "entry_ok":  direction != "none" and score >= SIGNAL_MIN_ENTRY,
        "price":     price,
        "rsi":       round(rsi, 1),
        "ema20":     round(ema20, 4),
        "ema50":     round(ema50, 4),
        "macd":      round(macd, 4),
        "macd_sig":  round(macd_sig, 4),
        "atr_pct":   round(atr_pct, 2),
        "mom_1h":    round(mom_1h, 3),
        "pct_from_high": round(pct_from_high, 2),
        "signals":   flags,
    }

    logger.info(
        f"Signal {coin}: {score}/100 {direction.upper()} | "
        f"RSI={rsi:.0f} EMA20={'✓' if price > ema20 else '✗'} "
        f"MACD={'✓' if macd > macd_sig else '✗'} mom={mom_1h:+.2f}%"
    )
    return result


def score_all() -> list[dict]:
    """Score all configured coins, return sorted list (best first)."""
    results = []
    for coin in COINS:
        candles = market.get_candles(coin)
        if not candles:
            logger.warning(f"No candles for {coin} — skipping")
            continue
        result = score_coin(coin, candles)
        results.append(result)

    results.sort(key=lambda x: (-x["score"], x["coin"]))
    return results


def _no_signal(coin: str, reason: str) -> dict:
    logger.warning(f"Signal {coin}: no signal — {reason}")
    return {
        "coin": coin, "score": 0, "direction": "none",
        "entry_ok": False, "price": 0.0, "signals": {}, "reason": reason,
    }
