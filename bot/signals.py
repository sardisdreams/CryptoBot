"""
Trade signal engine — scores a candidate 0-100 using 4h OHLCV candles.
Score >= 50 means the setup is tradeable (auto-execute at >=55).
Stop and target are ATR-based (replaces fixed %) giving minimum 2:1 risk/reward.

Six conditions (total 100 pts):
  1. Trend    (25) — price above EMA50 (4h, ~8 days of context)
  2. RSI      (20) — 14-period RSI in 35-65 zone
  3. Dip      (15) — 3-12% pullback from 20-period (3.3d) high
  4. Momentum (15) — price > SMA10 > SMA20
  5. Macro    (15) — BTC regime
  6. Vol      (10) — ATR < 12% of price
"""
from bot.ohlcv import get_candles
from bot.logger import setup_logger

logger = setup_logger("signals")

MIN_ENTRY_SCORE = 50
ATR_STOP_MULT   = 1.5   # stop  = entry - 1.5 × ATR
ATR_TARGET_MULT = 3.0   # target = entry + 3.0 × ATR  (2:1 R/R minimum)


def score_entry(cg_id: str, symbol: str, current_price: float, btc_regime: str) -> dict:
    """
    Score a potential entry. Returns full signal dict including ATR-based
    stop_price, target_price, and human-readable conditions list.
    """
    candles = get_candles(cg_id)
    if len(candles) < 20:  # need at least 20 4h candles (~3.3 days)
        return _no_data(symbol, cg_id)

    closes = [c["close"] for c in candles]
    highs  = [c["high"]  for c in candles]

    score      = 0
    conditions = []

    # ── 1. TREND: price above EMA50 (25 pts) ──────────────────────────────────
    ema50 = _ema(closes, 50)
    if ema50:
        if current_price > ema50:
            score += 25
            conditions.append(f"✓ Above EMA50 ${ema50:.4f} — uptrend intact")
        else:
            gap = (ema50 - current_price) / ema50 * 100
            conditions.append(f"✗ {gap:.1f}% below EMA50 ${ema50:.4f} — downtrend, skip")

    # ── 2. RSI: 35-65 zone (20 pts) ───────────────────────────────────────────
    rsi = _rsi(closes)
    if rsi is not None:
        if 35 <= rsi <= 55:
            score += 20
            conditions.append(f"✓ RSI {rsi:.0f} — ideal entry zone")
        elif 55 < rsi <= 65:
            score += 10
            conditions.append(f"~ RSI {rsi:.0f} — momentum OK, near upper bound")
        elif rsi > 65:
            conditions.append(f"✗ RSI {rsi:.0f} — overbought, wait for pullback")
        else:
            conditions.append(f"✗ RSI {rsi:.0f} — weak / selling pressure")

    # ── 3. DIP ENTRY: 3-12% below 20-period high, not at 5-period (20h) high (15 pts) ──
    # Periods are 4h candles: 20p = 3.3 days, 5p = 20 hours
    high_20p    = max(highs[-20:]) if len(highs) >= 20 else max(highs)
    high_5p     = max(highs[-5:])  if len(highs) >= 5  else max(highs)
    pullback_20 = (high_20p - current_price) / high_20p * 100 if high_20p > 0 else 0
    pullback_5  = (high_5p  - current_price) / high_5p  * 100 if high_5p  > 0 else 0
    if 3 <= pullback_20 <= 12:
        if pullback_5 < 2:
            # Price is near/at the 20h high — at recent resistance, not a fresh dip
            score += 5
            conditions.append(
                f"~ {pullback_20:.1f}% off 20p high but only {pullback_5:.1f}% off 5p high "
                f"— price at recent top, not a clean dip entry"
            )
        else:
            score += 15
            conditions.append(f"✓ {pullback_20:.1f}% below 20p high, {pullback_5:.1f}% below 5p high — healthy dip")
    elif pullback_20 < 3:
        conditions.append(f"✗ Only {pullback_20:.1f}% off 20p high — chasing, poor R/R")
    else:
        conditions.append(f"✗ {pullback_20:.1f}% below 20p high — too damaged for 4h entry")

    # ── 4. MOMENTUM: price > SMA10 > SMA20 (15 pts) ──────────────────────────
    sma10 = _sma(closes, 10)
    sma20 = _sma(closes, 20)
    if sma10 and sma20:
        if current_price > sma10 and sma10 > sma20:
            score += 15
            conditions.append(f"✓ Price > SMA10 > SMA20 — momentum aligned")
        elif current_price > sma10:
            score += 7
            conditions.append(f"~ Price > SMA10, SMA10 below SMA20 — early recovery")
        else:
            conditions.append(f"✗ Price < SMA10 — short-term momentum negative")

    # ── 5. MACRO: BTC regime (15 pts) ─────────────────────────────────────────
    if btc_regime in ("BULL", "STRONG_BULL"):
        score += 15
        conditions.append(f"✓ BTC {btc_regime} — macro tailwind")
    elif btc_regime == "NEUTRAL":
        score += 10
        conditions.append(f"~ BTC NEUTRAL — no macro headwind")
    elif btc_regime == "BEAR":
        score += 5
        conditions.append(f"~ BTC BEAR — headwind, require stronger setup")
    else:
        conditions.append(f"✗ BTC STRONG_BEAR — avoid new entries")

    # ── 6. VOLATILITY: ATR < 12% (10 pts) ────────────────────────────────────
    atr     = _atr(candles[-15:])
    atr_pct = (atr / current_price * 100) if atr and current_price > 0 else None
    if atr_pct is not None:
        if atr_pct < 8:
            score += 10
            conditions.append(f"✓ ATR {atr_pct:.1f}% — stable, tight stops")
        elif atr_pct < 12:
            score += 5
            conditions.append(f"~ ATR {atr_pct:.1f}% — moderate volatility")
        else:
            conditions.append(f"✗ ATR {atr_pct:.1f}% — high volatility, hard to size")

    # ── Stop / Target (ATR-based) ──────────────────────────────────────────────
    stop_price = target_price = stop_pct = target_pct = None
    if atr and current_price > 0:
        stop_price   = round(current_price - ATR_STOP_MULT * atr, 8)
        target_price = round(current_price + ATR_TARGET_MULT * atr, 8)
        stop_pct     = round((current_price - stop_price) / current_price * 100, 1)
        target_pct   = round((target_price - current_price) / current_price * 100, 1)

    entry_ok = score >= MIN_ENTRY_SCORE
    logger.info(f"Signal {symbol}: {score}/100 entry_ok={entry_ok}")

    return {
        "symbol":       symbol,
        "cg_id":        cg_id,
        "score":        score,
        "entry_ok":     entry_ok,
        "stop_price":   stop_price,
        "target_price": target_price,
        "stop_pct":     stop_pct,
        "target_pct":   target_pct,
        "rsi":          round(rsi, 1) if rsi is not None else None,
        "ema50":        round(ema50, 6) if ema50 else None,
        "atr_pct":      round(atr_pct, 2) if atr_pct else None,
        "conditions":   conditions,
    }


def score_candidates(candidates: list[dict], btc_regime: str) -> list[dict]:
    """
    Score a list of screener candidates. Returns list sorted by score descending.
    Adds a 'signal' key to each candidate dict.
    OHLCV is cached 1h per token so subsequent ticks are fast.
    """
    to_score   = [c for c in candidates if c.get("cg_id") and c.get("price", 0) > 0]
    skip       = [c for c in candidates if c not in to_score]

    scored = []
    for c in to_score:
        sig = score_entry(c["cg_id"], c.get("symbol", "?"), c["price"], btc_regime)
        scored.append({**c, "signal": sig})

    for c in skip:
        scored.append({**c, "signal": {"score": 0, "entry_ok": False, "conditions": ["not scored"]}})

    return sorted(scored, key=lambda x: x["signal"]["score"], reverse=True)


# ── Internal indicator helpers ─────────────────────────────────────────────────

def _ema(prices: list[float], period: int) -> float | None:
    if len(prices) < period:
        return None
    k   = 2 / (period + 1)
    val = sum(prices[:period]) / period
    for p in prices[period:]:
        val = p * k + val * (1 - k)
    return round(val, 8)


def _sma(prices: list[float], period: int) -> float | None:
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period


def _rsi(prices: list[float], period: int = 14) -> float | None:
    if len(prices) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(prices)):
        d = prices[i] - prices[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    if al == 0:
        return 100.0
    return round(100 - (100 / (1 + ag / al)), 2)


def _atr(candles: list[dict], period: int = 14) -> float | None:
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        high = candles[i]["high"]
        low  = candles[i]["low"]
        prev = candles[i - 1]["close"]
        trs.append(max(high - low, abs(high - prev), abs(low - prev)))
    if len(trs) < period:
        return None
    return sum(trs[-period:]) / period


def check_held_positions(open_positions: dict, prices: dict, btc_regime: str) -> list[dict]:
    """
    Score each held position using daily OHLCV. Return soft exit suggestions
    for any position whose daily setup has deteriorated (score <= 25).
    Replaces the old time-window expiry logic.
    """
    SKIP_SYMS = {"USDC", "USDT", "DAI", "WETH", "ETH"}
    suggestions = []
    for symbol, lots in open_positions.items():
        if symbol in SKIP_SYMS:
            continue
        cg_id = next((lot["cg_id"] for lot in lots if lot.get("cg_id")), None)
        if not cg_id:
            continue
        price = prices.get(symbol, 0)
        if price <= 0:
            continue
        sig = score_entry(cg_id, symbol, price, btc_regime)
        if sig["score"] <= 25:
            total_tokens = sum(lot["amount_tokens"] for lot in lots)
            failing = [c for c in sig.get("conditions", []) if c.startswith("✗")]
            suggestions.append({
                "symbol":        symbol,
                "amount_tokens": total_tokens,
                "reason":        (
                    f"{symbol} signal deteriorated: {sig['score']}/100 — "
                    + "; ".join(failing)
                ),
                "urgency":       "low",
                "exit_type":     "signal_suggestion",
                "signal_score":  sig["score"],
            })
    return suggestions


def _no_data(symbol: str, cg_id: str) -> dict:
    return {
        "symbol": symbol, "cg_id": cg_id,
        "score": 0, "entry_ok": False,
        "stop_price": None, "target_price": None,
        "stop_pct": None, "target_pct": None,
        "rsi": None, "ema50": None, "atr_pct": None,
        "conditions": ["insufficient 4h OHLCV data (< 20 candles)"],
    }
