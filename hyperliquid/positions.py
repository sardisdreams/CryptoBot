"""
Hyperliquid position tracking — local records of open perp positions.

Hyperliquid is the source of truth for actual on-chain state.
This file tracks bot-level metadata: entry reasoning, TP/SL levels,
order IDs for cancellation, hold time, etc.

All writes are atomic (write-to-tmp + os.replace).
"""
import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path

POSITIONS_FILE    = "data/hl_positions.json"
REALIZED_FILE     = "records/hl_realized_gains.csv"
REALIZED_HEADERS  = [
    "date_opened", "date_closed", "coin", "direction", "size_coins",
    "cost_basis_usd", "proceeds_usd", "gain_loss_usd", "gain_loss_pct",
    "hold_hours", "entry_price", "exit_price", "leverage",
    "entry_reasoning", "exit_tx",
]


def get_open_positions() -> dict:
    """Return {coin: position_dict} for all open positions."""
    return _load()


def open_position(
    coin: str,
    direction: str,
    size_coins: float,
    entry_price: float,
    cost_basis_usd: float,
    tp_price: float,
    sl_price: float,
    leverage: int,
    reasoning: str = "",
    order_id: str = "",
) -> dict:
    """Record a newly opened position."""
    data = _load()
    pos = {
        "coin":           coin,
        "direction":      direction,
        "size_coins":     size_coins,
        "entry_price":    entry_price,
        "cost_basis_usd": cost_basis_usd,
        "tp_price":       tp_price,
        "sl_price":       sl_price,
        "leverage":       leverage,
        "opened_at":      _now(),
        "reasoning":      reasoning,
        "order_id":       order_id,
        "tp_order_id":    "",
        "sl_order_id":    "",
    }
    data[coin] = pos
    _save(data)
    return pos


def close_position(
    coin: str,
    exit_price: float,
    proceeds_usd: float,
    exit_tx: str = "",
    exit_reason: str = "",
) -> dict | None:
    """Record a closed position. Returns realized P&L dict or None if not found."""
    data = _load()
    pos = data.pop(coin, None)
    if not pos:
        return None
    _save(data)

    cost    = pos["cost_basis_usd"]
    gain    = proceeds_usd - cost
    pct     = (gain / cost * 100) if cost > 0 else 0
    opened  = datetime.fromisoformat(pos["opened_at"])
    closed  = datetime.now(timezone.utc)
    hours   = (closed - opened).total_seconds() / 3600

    row = {
        "date_opened":    pos["opened_at"],
        "date_closed":    closed.isoformat(),
        "coin":           coin,
        "direction":      pos["direction"],
        "size_coins":     pos["size_coins"],
        "cost_basis_usd": round(cost, 4),
        "proceeds_usd":   round(proceeds_usd, 4),
        "gain_loss_usd":  round(gain, 4),
        "gain_loss_pct":  round(pct, 2),
        "hold_hours":     round(hours, 1),
        "entry_price":    pos["entry_price"],
        "exit_price":     exit_price,
        "leverage":       pos["leverage"],
        "entry_reasoning": pos.get("reasoning", ""),
        "exit_tx":        exit_tx,
    }
    _append_realized(row)
    return row


def update_tp_sl_orders(coin: str, tp_order_id: str = "", sl_order_id: str = ""):
    """Store TP/SL order IDs after they are placed on Hyperliquid."""
    data = _load()
    if coin in data:
        if tp_order_id:
            data[coin]["tp_order_id"] = tp_order_id
        if sl_order_id:
            data[coin]["sl_order_id"] = sl_order_id
        _save(data)


def get_position_summary(prices: dict[str, float]) -> list[dict]:
    """Return open positions enriched with current P&L."""
    data = _load()
    summary = []
    for coin, pos in data.items():
        price   = prices.get(coin, 0)
        cost    = pos["cost_basis_usd"]
        size    = pos["size_coins"]
        lev     = pos.get("leverage", 1)
        if price > 0 and size != 0 and cost > 0:
            notional = abs(size) * price
            if pos["direction"] == "long":
                pnl = (price - pos["entry_price"]) / pos["entry_price"] * cost * lev
            else:
                pnl = (pos["entry_price"] - price) / pos["entry_price"] * cost * lev
            pct = pnl / cost * 100
        else:
            pnl = 0.0
            pct = 0.0

        opened   = datetime.fromisoformat(pos["opened_at"])
        hold_hrs = (datetime.now(timezone.utc) - opened).total_seconds() / 3600

        summary.append({
            **pos,
            "current_price":  price,
            "unrealized_pnl": round(pnl, 2),
            "gain_loss_pct":  round(pct, 2),
            "hold_hours":     round(hold_hrs, 1),
        })
    return summary


def get_recent_trades(n: int = 20) -> list[dict]:
    """Return the last N closed trades from the CSV."""
    if not os.path.exists(REALIZED_FILE):
        return []
    with open(REALIZED_FILE, newline="") as f:
        rows = list(csv.DictReader(f))
    return rows[-n:]


# ── Internal ──────────────────────────────────────────────────────────────────

def _load() -> dict:
    if not os.path.exists(POSITIONS_FILE):
        return {}
    with open(POSITIONS_FILE) as f:
        return json.load(f)


def _save(data: dict):
    os.makedirs("data", exist_ok=True)
    tmp = POSITIONS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, POSITIONS_FILE)


def _append_realized(row: dict):
    os.makedirs("records", exist_ok=True)
    write_header = not os.path.exists(REALIZED_FILE)
    with open(REALIZED_FILE, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=REALIZED_HEADERS)
        if write_header:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in REALIZED_HEADERS})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
