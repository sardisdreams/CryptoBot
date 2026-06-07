"""
Tracks API and operational costs to show net profit after expenses.
Persists to data/costs.json.
"""
import json
import os
from datetime import datetime, timezone

COSTS_FILE = "data/costs.json"


def _load() -> dict:
    if not os.path.exists(COSTS_FILE):
        return {"anthropic": {"total": 0.0, "daily": {}}, "gas": {"total_eth": 0.0, "total_usd": 0.0}}
    with open(COSTS_FILE) as f:
        return json.load(f)


def _save(data: dict):
    os.makedirs("data", exist_ok=True)
    with open(COSTS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def record_anthropic(cost_usd: float, model: str = ""):
    data  = _load()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    data["anthropic"]["total"] = round(data["anthropic"]["total"] + cost_usd, 6)
    daily = data["anthropic"].setdefault("daily", {})
    daily[today] = round(daily.get(today, 0.0) + cost_usd, 6)
    # Keep only last 30 days
    if len(daily) > 30:
        oldest = sorted(daily.keys())[0]
        del daily[oldest]
    _save(data)


def record_gas(gas_eth: float, eth_price_usd: float):
    data = _load()
    data["gas"]["total_eth"] = round(data["gas"].get("total_eth", 0.0) + gas_eth, 8)
    data["gas"]["total_usd"] = round(data["gas"].get("total_usd", 0.0) + gas_eth * eth_price_usd, 4)
    _save(data)


def get_summary(eth_price_usd: float = 0.0) -> dict:
    data = _load()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    anthropic_total = data["anthropic"].get("total", 0.0)
    anthropic_today = data["anthropic"].get("daily", {}).get(today, 0.0)

    # Gas: use stored USD if available, else estimate from ETH
    gas_total_usd = data["gas"].get("total_usd", 0.0)
    if gas_total_usd == 0 and eth_price_usd > 0:
        gas_total_usd = data["gas"].get("total_eth", 0.0) * eth_price_usd

    total_costs = round(anthropic_total + gas_total_usd, 2)

    # 7-day and current-month Anthropic spend
    daily = data["anthropic"].get("daily", {})
    days_7d = sorted(daily.keys())[-7:]
    anthropic_7d = round(sum(daily[d] for d in days_7d), 4)

    this_month = datetime.now(timezone.utc).strftime("%Y-%m")
    anthropic_month = round(sum(v for k, v in daily.items() if k.startswith(this_month)), 4)

    return {
        "anthropic_total":  round(anthropic_total, 4),
        "anthropic_today":  round(anthropic_today, 4),
        "anthropic_7d":     anthropic_7d,
        "anthropic_month":  anthropic_month,
        "gas_total_usd":    round(gas_total_usd, 2),
        "total_costs":      total_costs,
        "alchemy":          0.0,   # free tier
        "coingecko":        0.0,   # free tier
    }
