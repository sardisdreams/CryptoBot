"""
Risk management guards — called each cycle before the agent opens new positions.
Returns (allowed: bool, reason: str).
"""
import csv
import json
import os
from datetime import datetime, timezone

REALIZED_GAINS_FILE = "records/realized_gains.csv"
PORTFOLIO_SNAPSHOT_FILE = "data/portfolio_snapshots.json"

DAILY_DRAWDOWN_LIMIT = 0.10   # halt if portfolio drops >10% in a single day
WIN_RATE_MIN        = 0.40    # pause if win rate < 40% over last 10 closed trades
WIN_RATE_LOOKBACK   = 10


def record_portfolio_value(total_usd: float):
    """Record portfolio value once per day for drawdown tracking."""
    data = _load_snapshots()
    today = _today()
    if today not in data:
        data[today] = total_usd
        _save_snapshots(data)


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
    trades = _load_recent_trades(WIN_RATE_LOOKBACK)
    if len(trades) < WIN_RATE_LOOKBACK:
        return True, ""  # not enough history yet
    wins = sum(1 for t in trades if float(t.get("gain_loss_pct", 0)) >= 0)
    rate = wins / len(trades)
    if rate < WIN_RATE_MIN:
        return False, (
            f"Win rate too low: {wins}/{len(trades)} ({rate:.0%}) over last {WIN_RATE_LOOKBACK} trades. "
            f"Holding cash until patterns improve. Review knowledge base for losing patterns."
        )
    return True, ""


def can_open_trade(current_portfolio_usd: float) -> tuple[bool, str]:
    """Combined guard — call this before opening any new position."""
    ok, reason = check_daily_drawdown(current_portfolio_usd)
    if not ok:
        return False, reason
    ok, reason = check_win_rate()
    if not ok:
        return False, reason
    return True, ""


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _load_snapshots() -> dict:
    if not os.path.exists(PORTFOLIO_SNAPSHOT_FILE):
        return {}
    with open(PORTFOLIO_SNAPSHOT_FILE) as f:
        return json.load(f)


def _save_snapshots(data: dict):
    os.makedirs("data", exist_ok=True)
    # Keep only last 30 days
    if len(data) > 30:
        oldest = sorted(data.keys())[0]
        del data[oldest]
    with open(PORTFOLIO_SNAPSHOT_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _load_recent_trades(n: int) -> list[dict]:
    if not os.path.exists(REALIZED_GAINS_FILE):
        return []
    with open(REALIZED_GAINS_FILE, newline="") as f:
        rows = list(csv.DictReader(f))
    return rows[-n:]
