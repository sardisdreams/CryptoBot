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
]


# ── Internal helpers ──────────────────────────────────────────────────────────

def _load() -> dict:
    if not os.path.exists(POSITIONS_FILE):
        return {}
    with open(POSITIONS_FILE) as f:
        return json.load(f)


def _save(data: dict):
    os.makedirs("data", exist_ok=True)
    with open(POSITIONS_FILE, "w") as f:
        json.dump(data, f, indent=2)


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
) -> str:
    """Record a new buy. Returns the position ID."""
    positions = _load()
    if symbol not in positions:
        positions[symbol] = []

    position_id = str(uuid.uuid4())[:8]
    positions[symbol].append({
        "id":              position_id,
        "symbol":          symbol,
        "date_opened":     _now(),
        "entry_price_usd": round(entry_price_usd, 6),
        "amount_tokens":   amount_tokens,
        "cost_basis_usd":  round(amount_tokens * entry_price_usd, 4),
        "entry_tx":        tx_hash,
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
            "date_opened":   lot["date_opened"],
            "date_closed":   date_closed,
            "token":         symbol,
            "amount_tokens": round(used, 8),
            "cost_basis_usd": cost_basis,
            "proceeds_usd":  proceeds,
            "gain_loss_usd": gain_loss,
            "gain_loss_pct": gain_pct,
            "hold_days":     hold_days,
            "term":          term,
            "entry_tx":      lot["entry_tx"],
            "exit_tx":       exit_tx,
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


# ── Query helpers ─────────────────────────────────────────────────────────────

def get_open_positions() -> dict:
    return _load()


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

            summary.append({
                "id":             lot["id"],
                "symbol":         symbol,
                "date_opened":    lot["date_opened"],
                "entry_price":    lot["entry_price_usd"],
                "amount_tokens":  lot["amount_tokens"],
                "cost_basis_usd": lot["cost_basis_usd"],
                "current_price":  current_price,
                "current_value":  round(current_value, 4),
                "gain_loss_usd":  round(gain_loss, 4),
                "gain_loss_pct":  round(gain_pct, 2),
                "hold_days":      hold_days,
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
