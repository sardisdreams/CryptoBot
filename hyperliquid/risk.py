"""
Risk guards for the Hyperliquid bot.

Enforced in code — Claude cannot override these.
Leverage amplifies losses, so limits are tighter than the Base bot.
"""
import csv
import json
import os
from datetime import datetime, timezone

from hyperliquid.config import (
    DAILY_DRAWDOWN_LIMIT, WIN_RATE_MIN, WIN_RATE_LOOKBACK, COOLDOWN_MINUTES
)

SNAPSHOTS_FILE = "data/hl_portfolio_snapshots.json"
COOLDOWN_FILE  = "data/hl_stopout_cooldowns.json"
REALIZED_FILE  = "records/hl_realized_gains.csv"


def record_portfolio_value(total_usd: float):
    data = _load_snapshots()
    today = _today()
    if today not in data:
        data[today] = total_usd
        _save_snapshots(data)


def record_stopout(coin: str):
    data = _load_cooldowns()
    data[coin.upper()] = datetime.now(timezone.utc).isoformat()
    _save_cooldowns(data)


def check_stopout_cooldown(coin: str) -> tuple[bool, str]:
    data = _load_cooldowns()
    ts   = data.get(coin.upper())
    if not ts:
        return True, ""
    try:
        elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(ts)).total_seconds() / 60
        if elapsed < COOLDOWN_MINUTES:
            return False, f"{coin} in stop-out cooldown: {COOLDOWN_MINUTES - elapsed:.0f}min remaining"
    except Exception:
        pass
    return True, ""


def check_daily_drawdown(current_usd: float) -> tuple[bool, str]:
    data    = _load_snapshots()
    day_start = data.get(_today())
    if not day_start or day_start <= 0:
        return True, ""
    drawdown = (day_start - current_usd) / day_start
    if drawdown >= DAILY_DRAWDOWN_LIMIT:
        return False, (
            f"HL daily drawdown limit hit: down {drawdown:.1%} today "
            f"(${day_start:.2f} → ${current_usd:.2f}). No new trades until tomorrow."
        )
    return True, ""


def check_win_rate() -> tuple[bool, str]:
    trades = _load_recent_trades(WIN_RATE_LOOKBACK)
    if len(trades) < WIN_RATE_LOOKBACK:
        return True, ""
    wins = sum(1 for t in trades if float(t.get("gain_loss_pct", 0)) >= 0)
    rate = wins / len(trades)
    if rate < WIN_RATE_MIN:
        return False, (
            f"HL win rate too low: {wins}/{len(trades)} ({rate:.0%}) "
            f"over last {WIN_RATE_LOOKBACK} trades."
        )
    return True, ""


def check_max_positions(open_count: int, max_open: int) -> tuple[bool, str]:
    if open_count >= max_open:
        return False, f"HL max positions reached: {open_count}/{max_open}"
    return True, ""


def can_open_trade(
    current_usd: float,
    open_count:  int,
    max_open:    int,
    coin:        str = "",
) -> tuple[bool, str]:
    for check_fn, args in [
        (check_daily_drawdown, (current_usd,)),
        (check_win_rate,       ()),
        (check_max_positions,  (open_count, max_open)),
    ]:
        ok, reason = check_fn(*args)
        if not ok:
            return False, reason
    if coin:
        ok, reason = check_stopout_cooldown(coin)
        if not ok:
            return False, reason
    return True, ""


def get_risk_summary(current_usd: float, open_count: int, max_open: int) -> dict:
    dd_ok, dd_reason = check_daily_drawdown(current_usd)
    wr_ok, wr_reason = check_win_rate()
    mp_ok, mp_reason = check_max_positions(open_count, max_open)
    trades = _load_recent_trades(WIN_RATE_LOOKBACK)
    wins   = sum(1 for t in trades if float(t.get("gain_loss_pct", 0)) >= 0)
    return {
        "drawdown_ok":  dd_ok,
        "win_rate_ok":  wr_ok,
        "positions_ok": mp_ok,
        "open_count":   open_count,
        "max_open":     max_open,
        "recent_wins":  wins,
        "recent_total": len(trades),
        "all_clear":    dd_ok and wr_ok and mp_ok,
    }


# ── Internal ──────────────────────────────────────────────────────────────────

def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _load_snapshots() -> dict:
    if not os.path.exists(SNAPSHOTS_FILE):
        return {}
    with open(SNAPSHOTS_FILE) as f:
        return json.load(f)


def _save_snapshots(data: dict):
    os.makedirs("data", exist_ok=True)
    if len(data) > 30:
        del data[sorted(data)[0]]
    tmp = SNAPSHOTS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, SNAPSHOTS_FILE)


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
    if not os.path.exists(REALIZED_FILE):
        return []
    with open(REALIZED_FILE, newline="") as f:
        return list(csv.DictReader(f))[-n:]
