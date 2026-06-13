"""
Dynamic capital management.

Floor = BASE_FLOOR + locked profit (10% of each realized win).
Floor only grows — losses never reduce it.
Max deploy = total portfolio - floor (everything above floor is fair game).
Recovery mode: if USDC reserve < floor, no new trades.
Trade size: 5-10% of total portfolio, scaled by conviction.
"""
import json
import os

CAPITAL_FILE  = "data/capital.json"
BASE_FLOOR    = 100.0   # starting floor — realistic given current portfolio state
PROFIT_LOCK   = 0.10    # 10% of each realized profit locked into floor


def _load() -> dict:
    if not os.path.exists(CAPITAL_FILE):
        return {"base_floor": BASE_FLOOR, "locked_profit": 0.0}
    with open(CAPITAL_FILE) as f:
        return json.load(f)


def _save(data: dict):
    os.makedirs("data", exist_ok=True)
    with open(CAPITAL_FILE, "w") as f:
        json.dump(data, f, indent=2)


def get_floor() -> float:
    """Current floor = base + accumulated locked profit."""
    d = _load()
    return round(d["base_floor"] + d["locked_profit"], 2)


def get_locked_profit() -> float:
    return round(_load()["locked_profit"], 2)


def get_withdrawable() -> float:
    """Amount above the base floor that has been locked in — safe to withdraw."""
    return round(_load()["locked_profit"], 2)


def lock_profit(gain_usd: float):
    """Call after a profitable trade close. Locks 10% of gain into floor."""
    if gain_usd <= 0:
        return
    d = _load()
    d["locked_profit"] = round(d["locked_profit"] + gain_usd * PROFIT_LOCK, 4)
    _save(d)


def get_max_deploy(total_portfolio_usd: float) -> float:
    """Everything above the floor can be deployed."""
    floor = get_floor()
    return max(0.0, round(total_portfolio_usd - floor, 2))


def get_trade_size_range(total_portfolio_usd: float) -> tuple[float, float]:
    """Min and max trade size: 5-15% of total portfolio, clamped to sensible bounds."""
    min_trade = max(20.0, round(total_portfolio_usd * 0.05, 0))
    max_trade = max(min_trade, round(total_portfolio_usd * 0.15, 0))
    # Hard caps: never trade less than $20 or more than $150
    min_trade = min(min_trade, 50.0)
    max_trade = min(max_trade, 150.0)
    return min_trade, max_trade


def is_in_recovery(usdc_balance_usd: float) -> bool:
    """True if USDC reserve is below floor — halt new trades."""
    return usdc_balance_usd < get_floor()


def get_summary(total_portfolio_usd: float, usdc_balance_usd: float) -> dict:
    floor         = get_floor()
    withdrawable  = get_withdrawable()
    max_deploy    = get_max_deploy(total_portfolio_usd)
    in_recovery   = is_in_recovery(usdc_balance_usd)
    min_trade, max_trade = get_trade_size_range(total_portfolio_usd)
    return {
        "floor":          floor,
        "base_floor":     BASE_FLOOR,
        "locked_profit":  get_locked_profit(),
        "withdrawable":   withdrawable,
        "max_deploy":     max_deploy,
        "in_recovery":    in_recovery,
        "min_trade":      min_trade,
        "max_trade":      max_trade,
    }
