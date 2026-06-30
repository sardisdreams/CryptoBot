"""
Risk management guards — called each cycle before the agent opens new positions.
Returns (allowed: bool, reason: str).
"""
import csv
import json
import os
from datetime import datetime, timezone

REALIZED_GAINS_FILE    = "records/realized_gains.csv"
PORTFOLIO_SNAPSHOT_FILE = "data/portfolio_snapshots.json"
COOLDOWN_FILE          = "data/stopout_cooldowns.json"

DAILY_DRAWDOWN_LIMIT = 0.10   # halt if portfolio drops >10% in a single day
WIN_RATE_MIN         = 0.40   # pause if win rate < 40% over last 5 closed trades
WIN_RATE_LOOKBACK    = 5
COOLDOWN_MINUTES     = 30     # no re-entry into a token for 30min after stop-out

# Regime multipliers — available USDC is the real constraint; regime only trims slightly
_REGIME_MULTIPLIERS = {
    "STRONG_BEAR": 0.80,
    "BEAR":        0.90,
    "NEUTRAL":     1.00,
    "BULL":        1.00,
    "STRONG_BULL": 1.00,
}
MAX_OPEN_POSITIONS = 12  # fallback default (neutral market, ~$1000 portfolio)


def get_position_cap(portfolio_usd: float, regime_label: str = "NEUTRAL") -> int:
    """
    Position cap scales with portfolio size. Regime applies only a light trim —
    available USDC is the real limiting factor, not slot count.
    Underwater positions should not block new opportunities.
    """
    if portfolio_usd < 500:
        base = 12
    elif portfolio_usd < 2_000:
        base = 15
    elif portfolio_usd < 10_000:
        base = 20
    else:
        base = 30
    mult = _REGIME_MULTIPLIERS.get(regime_label, 1.00)
    return max(6, int(base * mult))


def record_portfolio_value(total_usd: float):
    """Record portfolio value once per day for drawdown tracking."""
    data = _load_snapshots()
    today = _today()
    if today not in data:
        data[today] = total_usd
        _save_snapshots(data)


def record_stopout(symbol: str):
    """Record that a token was stopped out — starts the cooldown timer."""
    data = _load_cooldowns()
    data[symbol.upper()] = datetime.now(timezone.utc).isoformat()
    _save_cooldowns(data)


def check_stopout_cooldown(symbol: str) -> tuple[bool, str]:
    """Return (ok, reason). ok=False means this token is in cooldown after a stop-out."""
    data = _load_cooldowns()
    ts_str = data.get(symbol.upper())
    if not ts_str:
        return True, ""
    try:
        stopped_at = datetime.fromisoformat(ts_str)
        now = datetime.now(timezone.utc)
        elapsed = (now - stopped_at).total_seconds() / 60
        if elapsed < COOLDOWN_MINUTES:
            remaining = COOLDOWN_MINUTES - elapsed
            return False, (
                f"{symbol} is in stop-out cooldown: {remaining:.0f}min remaining. "
                f"Stopped out {elapsed:.0f}min ago — no re-entry until cooldown expires."
            )
    except Exception:
        pass
    return True, ""


def check_max_positions(open_position_count: int, portfolio_usd: float = 500.0, regime_label: str = "NEUTRAL") -> tuple[bool, str]:
    """Return (ok, reason). ok=False if at maximum simultaneous positions for current portfolio/regime."""
    cap = get_position_cap(portfolio_usd, regime_label)
    if open_position_count >= cap:
        return False, (
            f"Maximum positions reached: {open_position_count}/{cap} open "
            f"({regime_label} regime, ${portfolio_usd:.0f} portfolio). "
            f"Wait for a position to close before opening another."
        )
    return True, ""


def check_daily_drawdown(current_usd: float) -> tuple[bool, str]:
    """Return (ok, reason). ok=False means halt new trades."""
    data = _load_snapshots()
    today = _today()
    day_start = data.get(today)
    if not day_start or day_start <= 0:
        return True, ""
    drawdown = (day_start - current_usd) / day_start
    if drawdown >= DAILY_DRAWDOWN_LIMIT:
        return False, (
            f"Daily drawdown limit hit: portfolio down {drawdown:.1%} today "
            f"(${day_start:.2f} → ${current_usd:.2f}). No new trades until tomorrow."
        )
    return True, ""


def check_win_rate() -> tuple[bool, str]:
    """Return (ok, reason). ok=False means pause new entries."""
    # WETH/ETH trades are excluded — those losses came from untracked lots reconstructed
    # after downtime, not the current signal-based system. Mixing them in distorts the
    # win rate for altcoin momentum trades which is what this guard is designed to measure.
    all_trades = _load_recent_trades(WIN_RATE_LOOKBACK * 3)
    trades = [t for t in all_trades if t.get("token", "").upper() not in {"WETH", "ETH"}][-WIN_RATE_LOOKBACK:]
    if len(trades) < WIN_RATE_LOOKBACK:
        return True, ""
    wins = sum(1 for t in trades if float(t.get("gain_loss_pct", 0)) >= 0)
    rate = wins / len(trades)
    if rate < WIN_RATE_MIN:
        return False, (
            f"Win rate too low: {wins}/{len(trades)} ({rate:.0%}) over last {WIN_RATE_LOOKBACK} trades. "
            f"Holding cash until patterns improve. Review knowledge base for losing patterns."
        )
    return True, ""


def can_open_trade(
    current_portfolio_usd: float,
    open_position_count: int = 0,
    token_symbol: str = "",
    regime_label: str = "NEUTRAL",
) -> tuple[bool, str]:
    """Combined guard — call before opening any new position."""
    ok, reason = check_daily_drawdown(current_portfolio_usd)
    if not ok:
        return False, reason
    ok, reason = check_win_rate()
    if not ok:
        return False, reason
    ok, reason = check_max_positions(open_position_count, current_portfolio_usd, regime_label)
    if not ok:
        return False, reason
    if token_symbol:
        ok, reason = check_stopout_cooldown(token_symbol)
        if not ok:
            return False, reason
    return True, ""


def get_risk_summary(current_portfolio_usd: float, open_position_count: int, regime_label: str = "NEUTRAL") -> dict:
    """Return current risk state for inclusion in agent prompt."""
    dd_ok, dd_reason = check_daily_drawdown(current_portfolio_usd)
    wr_ok, wr_reason = check_win_rate()
    mp_ok, mp_reason = check_max_positions(open_position_count, current_portfolio_usd, regime_label)
    cap    = get_position_cap(current_portfolio_usd, regime_label)
    trades = _load_recent_trades(WIN_RATE_LOOKBACK)
    wins   = sum(1 for t in trades if float(t.get("gain_loss_pct", 0)) >= 0)
    streak = _get_current_streak()
    return {
        "drawdown_ok":   dd_ok,
        "win_rate_ok":   wr_ok,
        "positions_ok":  mp_ok,
        "open_count":    open_position_count,
        "max_positions": cap,
        "recent_wins":   wins,
        "recent_total":  len(trades),
        "streak":        streak,
        "all_clear":     dd_ok and wr_ok and mp_ok,
    }


def _get_current_streak() -> str:
    """Returns current win/loss streak as a string, e.g. 'W3' or 'L2'."""
    trades = _load_recent_trades(20)
    if not trades:
        return "—"
    streak_type = "W" if float(trades[-1].get("gain_loss_pct", 0)) >= 0 else "L"
    count = 0
    for t in reversed(trades):
        is_win = float(t.get("gain_loss_pct", 0)) >= 0
        if (is_win and streak_type == "W") or (not is_win and streak_type == "L"):
            count += 1
        else:
            break
    return f"{streak_type}{count}"


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _load_snapshots() -> dict:
    if not os.path.exists(PORTFOLIO_SNAPSHOT_FILE):
        return {}
    with open(PORTFOLIO_SNAPSHOT_FILE) as f:
        return json.load(f)


def _save_snapshots(data: dict):
    os.makedirs("data", exist_ok=True)
    if len(data) > 30:
        oldest = sorted(data.keys())[0]
        del data[oldest]
    tmp = PORTFOLIO_SNAPSHOT_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, PORTFOLIO_SNAPSHOT_FILE)


def _load_cooldowns() -> dict:
    if not os.path.exists(COOLDOWN_FILE):
        return {}
    with open(COOLDOWN_FILE) as f:
        return json.load(f)


def _save_cooldowns(data: dict):
    os.makedirs("data", exist_ok=True)
    tmp = COOLDOWN_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, COOLDOWN_FILE)


def _load_recent_trades(n: int) -> list[dict]:
    if not os.path.exists(REALIZED_GAINS_FILE):
        return []
    with open(REALIZED_GAINS_FILE, newline="") as f:
        rows = list(csv.DictReader(f))
    return rows[-n:]
