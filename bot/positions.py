import csv
import json
import os
import uuid
from datetime import datetime, timezone

POSITIONS_FILE    = "data/positions.json"
REALIZED_GAINS_FILE = "records/realized_gains.csv"

REALIZED_HEADERS = [
    "date_opened",
    "date_closed",
    "token",
    "amount_tokens",
    "cost_basis_usd",
    "proceeds_usd",
    "gain_loss_usd",
    "gain_loss_pct",
    "hold_days",
    "term",           # "short" (<365 days) or "long" (>=365 days)
    "entry_tx",
    "exit_tx",
    "entry_reasoning",
]


# ── Internal helpers ──────────────────────────────────────────────────────────

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
    os.replace(tmp, POSITIONS_FILE)  # atomic on all platforms


def _ensure_realized_file():
    os.makedirs("records", exist_ok=True)
    if not os.path.exists(REALIZED_GAINS_FILE):
        with open(REALIZED_GAINS_FILE, "w", newline="") as f:
            csv.writer(f).writerow(REALIZED_HEADERS)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Open a new position (buying a token) ─────────────────────────────────────

def open_position(
    symbol: str,
    amount_tokens: float,
    entry_price_usd: float,
    tx_hash: str,
    take_profit_pct: float = 25.0,
    stop_loss_pct: float = 25.0,
    reasoning: str = "",
    cg_id: str = "",
) -> str:
    """Record a new buy with TP/SL targets. Returns the position ID."""
    positions = _load()
    if symbol not in positions:
        positions[symbol] = []

    position_id       = str(uuid.uuid4())[:8]
    take_profit_price = round(entry_price_usd * (1 + take_profit_pct / 100), 6)
    stop_loss_price   = round(entry_price_usd * (1 - stop_loss_pct  / 100), 6)

    positions[symbol].append({
        "id":                position_id,
        "symbol":            symbol,
        "date_opened":       _now(),
        "entry_price_usd":   round(entry_price_usd, 6),
        "amount_tokens":     amount_tokens,
        "cost_basis_usd":    round(amount_tokens * entry_price_usd, 4),
        "entry_tx":          tx_hash,
        "take_profit_price": take_profit_price,
        "stop_loss_price":   stop_loss_price,
        "take_profit_pct":   take_profit_pct,
        "stop_loss_pct":     stop_loss_pct,
        "entry_reasoning":   reasoning,
        "cg_id":             cg_id,
    })
    _save(positions)
    return position_id


# ── Close a position (selling back to USDC) — FIFO ───────────────────────────

def close_position(
    symbol: str,
    amount_tokens: float,
    exit_price_usd: float,
    exit_tx: str,
) -> list[dict]:
    """
    Close amount_tokens of symbol using FIFO.
    Returns list of realized gain records for each lot closed.
    """
    _ensure_realized_file()
    positions = _load()
    lots = positions.get(symbol, [])

    if not lots:
        return []

    proceeds_per_token = exit_price_usd
    remaining = amount_tokens
    realized = []
    date_closed = _now()

    while remaining > 0 and lots:
        lot = lots[0]
        lot_amount = lot["amount_tokens"]

        if lot_amount <= remaining:
            # Close entire lot
            used = lot_amount
            lots.pop(0)
        else:
            # Partial close — split the lot
            used = remaining
            lots[0]["amount_tokens"] = round(lot_amount - used, 10)
            lots[0]["cost_basis_usd"] = round(lots[0]["amount_tokens"] * lot["entry_price_usd"], 4)

        remaining -= used

        cost_basis  = round(used * lot["entry_price_usd"], 4)
        proceeds    = round(used * proceeds_per_token, 4)
        gain_loss   = round(proceeds - cost_basis, 4)
        gain_pct    = round((gain_loss / cost_basis) * 100, 2) if cost_basis else 0

        date_open_dt   = datetime.fromisoformat(lot["date_opened"])
        date_close_dt  = datetime.fromisoformat(date_closed)
        hold_days      = (date_close_dt - date_open_dt).days
        term           = "long" if hold_days >= 365 else "short"

        record = {
            "date_opened":      lot["date_opened"],
            "date_closed":      date_closed,
            "token":            symbol,
            "amount_tokens":    round(used, 8),
            "cost_basis_usd":   cost_basis,
            "proceeds_usd":     proceeds,
            "gain_loss_usd":    gain_loss,
            "gain_loss_pct":    gain_pct,
            "hold_days":        hold_days,
            "term":             term,
            "entry_tx":         lot["entry_tx"],
            "exit_tx":          exit_tx,
            "entry_reasoning":  lot.get("entry_reasoning", ""),
        }
        realized.append(record)

        # Append to CSV
        with open(REALIZED_GAINS_FILE, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=REALIZED_HEADERS)
            writer.writerow(record)

    # Remove symbol key if no lots remain
    if not lots:
        positions.pop(symbol, None)
    else:
        positions[symbol] = lots

    _save(positions)
    return realized


def raise_take_profit(symbol: str, lot_id: str, multiplier: float = 1.5):
    """
    After a partial TP exit, raise the remaining lot's TP by multiplier
    so it doesn't immediately trigger again on the next tick.
    """
    all_positions = _load()
    lots = all_positions.get(symbol, [])
    for lot in lots:
        if lot.get("id") == lot_id and lot.get("take_profit_price"):
            old_tp = lot["take_profit_price"]
            lot["take_profit_price"] = round(old_tp * multiplier, 6)
            lot["take_profit_pct"]   = round(
                (lot["take_profit_price"] / lot["entry_price_usd"] - 1) * 100, 2
            )
    all_positions[symbol] = lots
    _save(all_positions)


# ── Query helpers ─────────────────────────────────────────────────────────────

def get_open_positions() -> dict:
    return _load()


def update_trailing_stops(current_prices: dict[str, float], trail_pct: float = 15.0):
    """
    Update trailing stop losses for all open positions.
    If the current price is higher than the recorded peak, raise the stop loss
    to trail_pct% below the new peak. Only raises stops — never lowers them.
    """
    all_positions = _load()
    changed = False
    for symbol, lots in all_positions.items():
        current_price = current_prices.get(symbol, 0)
        if current_price <= 0:
            continue
        for lot in lots:
            entry = lot.get("entry_price_usd", 0)
            if not entry:
                continue
            # Only activate trailing stop when position is up enough to matter (>8%)
            gain_pct = (current_price - entry) / entry * 100 if entry > 0 else 0
            if gain_pct < 8:
                continue
            peak = lot.get("highest_price_seen", entry)
            if current_price > peak:
                lot["highest_price_seen"] = round(current_price, 6)
                new_sl = round(current_price * (1 - trail_pct / 100), 6)
                old_sl = lot.get("stop_loss_price", 0)
                if new_sl > old_sl:
                    lot["stop_loss_price"] = new_sl
                    changed = True
    if changed:
        _save(all_positions)


def check_mechanical_exits(current_prices: dict[str, float]) -> list[dict]:
    """
    Check all open positions against TP/SL/time targets.
    Returns list of exits to execute — caller is responsible for executing swaps.
    Each item: {symbol, amount_tokens, reason, urgency}
    """
    # Update trailing stops before checking exits
    update_trailing_stops(current_prices)

    positions = _load()
    exits = []
    now = datetime.now(timezone.utc)

    for symbol, lots in positions.items():
        current_price = current_prices.get(symbol, 0)
        if current_price == 0:
            continue

        for lot in lots:
            tp    = lot.get("take_profit_price")
            sl    = lot.get("stop_loss_price")
            until = lot.get("max_hold_until")

            # Stop loss — sell entire lot immediately
            if sl and current_price <= sl:
                gain_pct = (current_price - lot["entry_price_usd"]) / lot["entry_price_usd"] * 100
                exits.append({
                    "symbol":        symbol,
                    "amount_tokens": lot["amount_tokens"],
                    "lot_id":        lot["id"],
                    "reason":        f"STOP LOSS hit at ${current_price:.4f} ({gain_pct:+.1f}%)",
                    "urgency":       "immediate",
                    "exit_type":     "stop_loss",
                })

            # Take profit — sell 50% of lot (100% if position too small to split)
            elif tp and current_price >= tp:
                gain_pct    = (current_price - lot["entry_price_usd"]) / lot["entry_price_usd"] * 100
                lot_value   = lot["amount_tokens"] * current_price
                sell_tokens = lot["amount_tokens"] if lot_value < 40 else lot["amount_tokens"] * 0.5
                exits.append({
                    "symbol":        symbol,
                    "amount_tokens": sell_tokens,
                    "lot_id":        lot["id"],
                    "reason":        f"TAKE PROFIT hit at ${current_price:.4f} ({gain_pct:+.1f}%)",
                    "urgency":       "normal",
                    "exit_type":     "take_profit",
                })

    return exits


def get_position_summary(current_prices: dict[str, float]) -> list[dict]:
    """Return all open positions with current P&L."""
    positions = _load()
    summary = []

    for symbol, lots in positions.items():
        current_price = current_prices.get(symbol, 0)
        for lot in lots:
            if current_price > 0:
                current_value = lot["amount_tokens"] * current_price
                gain_loss     = current_value - lot["cost_basis_usd"]
                gain_pct      = (gain_loss / lot["cost_basis_usd"]) * 100 if lot["cost_basis_usd"] else 0
            else:
                current_value = 0
                gain_loss     = 0
                gain_pct      = 0

            date_open = datetime.fromisoformat(lot["date_opened"])
            hold_days = (datetime.now(timezone.utc) - date_open).days

            tp_price = lot.get("take_profit_price")
            sl_price = lot.get("stop_loss_price")
            entry    = lot["entry_price_usd"]
            highest  = lot.get("highest_price_seen", entry)
            is_trailing_stop = highest > entry * 1.001  # trailing stop raised at some point

            # % distance from current price to TP/SL (positive = price needs to move that way)
            tp_distance_pct = ((tp_price - current_price) / current_price * 100) if (tp_price and current_price > 0) else None
            sl_distance_pct = ((current_price - sl_price) / current_price * 100) if (sl_price and current_price > 0) else None
            sl_breached     = (sl_price and current_price > 0 and current_price < sl_price)
            tp_hit          = (tp_price and current_price > 0 and current_price >= tp_price)

            max_hold_until = lot.get("max_hold_until")
            hold_expired   = False
            if max_hold_until:
                try:
                    hold_expired = datetime.now(timezone.utc) > datetime.fromisoformat(max_hold_until)
                except Exception:
                    pass

            summary.append({
                "id":                lot["id"],
                "symbol":            symbol,
                "date_opened":       lot["date_opened"],
                "entry_price":       entry,
                "amount_tokens":     lot["amount_tokens"],
                "cost_basis_usd":    lot["cost_basis_usd"],
                "current_price":     current_price,
                "current_value":     round(current_value, 4),
                "gain_loss_usd":     round(gain_loss, 4),
                "gain_loss_pct":     round(gain_pct, 2),
                "hold_days":         hold_days,
                "take_profit_price": tp_price,
                "stop_loss_price":   sl_price,
                "take_profit_pct":   lot.get("take_profit_pct", 25),
                "stop_loss_pct":     lot.get("stop_loss_pct", 25),
                "cg_id":             lot.get("cg_id", ""),
                "is_trailing_stop":  is_trailing_stop,
                "highest_price_seen": highest,
                "tp_distance_pct":   round(tp_distance_pct, 1) if tp_distance_pct is not None else None,
                "sl_distance_pct":   round(sl_distance_pct, 1) if sl_distance_pct is not None else None,
                "sl_breached":       sl_breached,
                "tp_hit":            tp_hit,
                "max_hold_until":    max_hold_until,
                "hold_expired":      hold_expired,
            })

    return summary


def get_realized_summary() -> dict:
    """Return summary stats from the realized gains CSV."""
    _ensure_realized_file()
    total_gain = 0.0
    short_term = 0.0
    long_term  = 0.0
    trades     = 0

    with open(REALIZED_GAINS_FILE, newline="") as f:
        for row in csv.DictReader(f):
            gain = float(row.get("gain_loss_usd", 0))
            total_gain += gain
            trades += 1
            if row.get("term") == "long":
                long_term += gain
            else:
                short_term += gain

    return {
        "total_realized_gain_usd": round(total_gain, 2),
        "short_term_gain_usd":     round(short_term, 2),
        "long_term_gain_usd":      round(long_term, 2),
        "total_trades_closed":     trades,
    }
