"""
Health monitor — runs every 30 minutes independently of the main bot.
Sends email alerts when something is wrong. Silent when all is well.

Checks:
  1. Bot service is running and ticking on schedule
  2. Anthropic API credit exhausted
  3. Token cache prices diverging >20% from live screener prices
  4. Open positions missing cg_id (cannot be priced or exited)
  5. USDC balance dropped >15% since last monitor run
"""
import json
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(__file__))

from bot.logger import setup_logger
from bot.emailer import send_alert

logger = setup_logger("monitor")

STATE_FILE  = "data/monitor_state.json"
LAST_TICK   = "data/last_tick.json"
POSITIONS   = "data/positions.json"
TOKEN_CACHE = "data/token_cache.json"
CREDIT_ALERT = "data/credit_alert.json"

# How long before we consider the bot "stuck" (3x the slowest tier interval)
MAX_TICK_GAP_MINUTES = 195  # 3 × 65min (CONSERVE + buffer)


def _load_json(path: str) -> dict | list:
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def _load_state() -> dict:
    return _load_json(STATE_FILE) or {}


def _save_state(state: dict):
    os.makedirs("data", exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_FILE)


def check_bot_alive() -> str | None:
    """Return alert message if bot hasn't ticked recently."""
    data = _load_json(LAST_TICK)
    if not data or not data.get("ts"):
        return "Bot has never completed a tick — may not have started correctly."
    try:
        last = datetime.fromisoformat(data["ts"])
        age_minutes = (datetime.now(timezone.utc) - last).total_seconds() / 60
        if age_minutes > MAX_TICK_GAP_MINUTES:
            return (
                f"Bot has not ticked in {age_minutes:.0f} minutes "
                f"(last tick: {data['ts'][:16]} UTC). "
                f"Expected every ~60min. May be stuck or crashed."
            )
    except Exception as e:
        return f"Could not parse last_tick.json: {e}"
    return None


def check_api_credit() -> str | None:
    """Return alert message if Anthropic API credit is exhausted."""
    data = _load_json(CREDIT_ALERT)
    if data.get("active"):
        return (
            f"Anthropic API credit exhausted (flagged at {data.get('ts','?')[:16]} UTC). "
            f"Bot is paused. Top up at console.anthropic.com."
        )
    return None


def check_position_cg_ids() -> str | None:
    """Return alert if any open position is missing a cg_id (can't be priced or exited)."""
    positions = _load_json(POSITIONS)
    if not positions:
        return None
    missing = []
    for symbol, lots in positions.items():
        for lot in lots:
            if not lot.get("cg_id"):
                missing.append(symbol)
                break
    if missing:
        return (
            f"Open positions missing cg_id (cannot be priced or auto-exited): "
            f"{', '.join(missing)}. Bot may be unable to manage these positions."
        )
    return None


def check_usdc_drop(state: dict) -> tuple[str | None, dict]:
    """Return alert if USDC balance dropped >15% since last monitor run."""
    try:
        from bot.config import BASE_RPC_URL, PRIVATE_KEY, TOKENS
        import requests, certifi
        from eth_account import Account
        from web3 import Web3

        wallet = Account.from_key(PRIVATE_KEY).address
        usdc_addr = TOKENS["USDC"]["address"]
        decimals  = TOKENS["USDC"]["decimals"]

        session = requests.Session()
        session.verify = certifi.where()
        w3 = Web3(Web3.HTTPProvider(BASE_RPC_URL, session=session))
        if not w3.is_connected():
            return None, state  # can't check, skip silently

        abi = [{"inputs": [{"name": "account", "type": "address"}],
                "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}],
                "stateMutability": "view", "type": "function"}]
        contract = w3.eth.contract(address=Web3.to_checksum_address(usdc_addr), abi=abi)
        balance  = contract.functions.balanceOf(Web3.to_checksum_address(wallet)).call()
        usdc_now = balance / (10 ** decimals)

        prev = state.get("usdc_balance", 0)
        state["usdc_balance"] = usdc_now

        if prev > 0:
            drop_pct = (prev - usdc_now) / prev
            if drop_pct > 0.15:
                return (
                    f"USDC balance dropped {drop_pct:.1%} since last monitor check: "
                    f"${prev:.2f} → ${usdc_now:.2f}. "
                    f"Verify this was an expected trade."
                ), state
    except Exception as e:
        logger.warning(f"USDC balance check failed: {e}")
    return None, state


def check_price_coherence() -> str | None:
    """Warn if any token cache price differs >20% from screener cache price."""
    token_cache = _load_json(TOKEN_CACHE)
    screener    = _load_json("data/screener_cache.json")
    if not token_cache or not screener:
        return None

    # Build symbol→price from screener candidates
    screener_prices = {}
    for coin_list in screener.values():
        if not isinstance(coin_list, list):
            continue
        for coin in coin_list:
            sym = coin.get("symbol", "").upper()
            price = coin.get("price", 0)
            if sym and price > 0:
                screener_prices[sym] = price

    diverged = []
    for cg_id, entry in token_cache.items():
        sym        = entry.get("symbol", "").upper()
        cache_price = entry.get("price", 0)
        live_price  = screener_prices.get(sym, 0)
        if cache_price > 0 and live_price > 0:
            diff = abs(cache_price - live_price) / live_price
            if diff > 0.20:
                diverged.append(
                    f"{sym}: cache=${cache_price:.4f} vs live=${live_price:.4f} "
                    f"({diff:.0%} diff)"
                )

    if diverged:
        return (
            f"Token cache prices diverging >20% from screener for: "
            f"{'; '.join(diverged[:5])}. "
            f"Agent may misjudge entry/exit levels."
        )
    return None


def run():
    state  = _load_state()
    alerts = []

    # Run all checks
    for check_fn in [check_bot_alive, check_api_credit, check_position_cg_ids, check_price_coherence]:
        result = check_fn()
        if result:
            alerts.append(result)

    usdc_alert, state = check_usdc_drop(state)
    if usdc_alert:
        alerts.append(usdc_alert)

    _save_state(state)

    if not alerts:
        logger.info("Health check passed — all systems nominal")
        return

    # Deduplicate against recently-sent alerts to avoid spam
    sent_recently = set(state.get("sent_alerts", []))
    new_alerts    = [a for a in alerts if a not in sent_recently]

    if not new_alerts:
        logger.info(f"Health issues detected but already alerted: {len(alerts)} issue(s)")
        return

    # Send email
    body = "\n\n".join(f"• {a}" for a in new_alerts)
    sent = send_alert(
        subject=f"CryptoBot health alert: {len(new_alerts)} issue(s)",
        body=f"Health monitor detected the following issues:\n\n{body}\n\nDashboard: http://143.198.37.28:5000",
    )
    if sent:
        state["sent_alerts"] = list(sent_recently | set(new_alerts))
        _save_state(state)
        logger.info(f"Alert email sent: {len(new_alerts)} issue(s)")
    else:
        logger.warning(f"Health issues found but email not configured: {new_alerts}")


if __name__ == "__main__":
    run()
