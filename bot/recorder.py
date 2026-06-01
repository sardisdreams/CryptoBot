import csv
import os
from datetime import datetime, timezone

RECORDS_DIR = "records"
RECORDS_FILE = os.path.join(RECORDS_DIR, "transactions.csv")

HEADERS = [
    "date_utc",
    "tx_hash",
    "type",
    "token_in",
    "amount_in",
    "token_out",
    "amount_out",
    "price_eth_usd",
    "gas_used",
    "gas_price_gwei",
    "gas_cost_eth",
    "status",
]


def _ensure_file():
    os.makedirs(RECORDS_DIR, exist_ok=True)
    if not os.path.exists(RECORDS_FILE):
        with open(RECORDS_FILE, "w", newline="") as f:
            csv.writer(f).writerow(HEADERS)


def record_transaction(
    tx_hash: str,
    token_in: str,
    amount_in: float,
    token_out: str,
    amount_out: float,
    price_eth_usd: float = 0.0,
    gas_used: int = 0,
    gas_price_gwei: float = 0.0,
    status: str = "pending",
):
    _ensure_file()
    gas_cost_eth = (gas_used * gas_price_gwei) / 1e9 if gas_used and gas_price_gwei else 0.0

    row = {
        "date_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "tx_hash": tx_hash,
        "type": "swap",
        "token_in": token_in,
        "amount_in": amount_in,
        "token_out": token_out,
        "amount_out": amount_out,
        "price_eth_usd": price_eth_usd,
        "gas_used": gas_used,
        "gas_price_gwei": round(gas_price_gwei, 4),
        "gas_cost_eth": round(gas_cost_eth, 8),
        "status": status,
    }

    with open(RECORDS_FILE, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS)
        writer.writerow(row)


def update_status(tx_hash: str, status: str, gas_used: int = 0):
    """Update a pending transaction's status once the receipt arrives."""
    _ensure_file()
    rows = []
    with open(RECORDS_FILE, "r", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["tx_hash"] == tx_hash:
                row["status"] = status
                if gas_used:
                    row["gas_used"] = gas_used
                    gwei = float(row["gas_price_gwei"])
                    row["gas_cost_eth"] = round((gas_used * gwei) / 1e9, 8)
            rows.append(row)

    with open(RECORDS_FILE, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS)
        writer.writeheader()
        writer.writerows(rows)
