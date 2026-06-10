import bot.ssl_fix  # must be first import — patches SSL before any HTTPS calls

import json
import os
import time
import certifi
import schedule
import requests
from datetime import datetime, timezone
from web3 import Web3
from dotenv import load_dotenv

from bot.config import BASE_RPC_URL, validate
from bot.logger import setup_logger
from bot.wallet import Wallet
from bot.market import Market
from bot.portfolio import Portfolio
from bot.executor import Executor
from bot.agent import TradingAgent
from bot.performance import get_tier
from bot import token_cache

load_dotenv()
logger = setup_logger("main")

os.makedirs("logs", exist_ok=True)
os.makedirs("records", exist_ok=True)
os.makedirs("data", exist_ok=True)


def _connect_rpc(max_retries: int = 5, delay: int = 10) -> Web3:
    """Connect to Base RPC with retries — handles temporary outages gracefully."""
    session = requests.Session()
    session.verify = certifi.where()
    for attempt in range(1, max_retries + 1):
        try:
            w3 = Web3(Web3.HTTPProvider(BASE_RPC_URL, session=session))
            if w3.is_connected():
                logger.info(f"Connected to Base chain (block {w3.eth.block_number})")
                return w3
        except Exception as e:
            logger.warning(f"RPC connection attempt {attempt}/{max_retries} failed: {e}")
        if attempt < max_retries:
            time.sleep(delay)
    logger.error(f"Could not connect to Base RPC after {max_retries} attempts")
    raise SystemExit(1)


def _backfill_position_cg_ids():
    """
    Global startup fix: for any open position missing a cg_id,
    find it in the token cache by symbol and patch it in.
    Ensures all positions are priceable and trackable — runs every startup.
    """
    pos_file = "data/positions.json"
    if not os.path.exists(pos_file):
        return
    with open(pos_file) as f:
        all_pos = json.load(f)
    changed = False
    cache_all = token_cache.list_all()
    # Build reverse lookup: symbol → cg_id
    sym_to_cg = {
        info.get("symbol", "").upper(): cg_id
        for cg_id, info in cache_all.items()
        if info.get("symbol")
    }
    for symbol, lots in all_pos.items():
        cg_id = sym_to_cg.get(symbol.upper())
        if not cg_id:
            continue
        for lot in lots:
            if not lot.get("cg_id"):
                lot["cg_id"] = cg_id
                changed = True
                logger.info(f"Backfilled cg_id for {symbol}: {cg_id}")
    if changed:
        tmp = pos_file + ".tmp"
        with open(tmp, "w") as f:
            json.dump(all_pos, f, indent=2)
        os.replace(tmp, pos_file)


def _reconcile_positions(w3):
    """
    On startup: query on-chain balance for every recorded position.
    If the wallet holds zero of a token we think we own, remove that lot
    from positions.json so the bot doesn't keep trying to manage a ghost.
    Skips removal if the RPC call fails (don't nuke valid positions due to
    a connection hiccup).
    """
    pos_file = "data/positions.json"
    if not os.path.exists(pos_file):
        return
    with open(pos_file) as f:
        all_pos = json.load(f)

    from bot.config import TOKENS
    from bot import token_cache as _tc

    ERC20_ABI = [{
        "inputs":  [{"name": "account", "type": "address"}],
        "name":    "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "type":    "function",
    }]

    from eth_account import Account
    from bot.config import PRIVATE_KEY
    wallet = Account.from_key(PRIVATE_KEY).address

    changed = False
    for symbol in list(all_pos.keys()):
        addr     = TOKENS.get(symbol, {}).get("address")
        decimals = TOKENS.get(symbol, {}).get("decimals", 18)
        if not addr:
            cached = _tc.get_by_symbol(symbol)
            if cached:
                addr     = cached.get("address")
                decimals = cached.get("decimals", 18)
        if not addr:
            logger.warning(f"Reconcile: no contract address for {symbol}, skipping")
            continue
        try:
            contract  = w3.eth.contract(address=w3.to_checksum_address(addr), abi=ERC20_ABI)
            bal_wei   = contract.functions.balanceOf(w3.to_checksum_address(wallet)).call()
            bal_units = bal_wei / (10 ** decimals)
        except Exception as e:
            logger.warning(f"Reconcile: could not query {symbol} balance — skipping ({e})")
            continue

        recorded = sum(lot["amount_tokens"] for lot in all_pos[symbol])
        if bal_units < recorded * 0.01:  # on-chain balance < 1% of what we think we hold
            logger.error(
                f"Reconcile: {symbol} on-chain balance {bal_units:.6f} vs recorded "
                f"{recorded:.6f} — removing ghost position"
            )
            del all_pos[symbol]
            changed = True
        elif abs(bal_units - recorded) / max(recorded, 1e-9) > 0.1:
            logger.warning(
                f"Reconcile: {symbol} mismatch — on-chain {bal_units:.6f} "
                f"vs recorded {recorded:.6f} (>10% diff, investigate)"
            )

    if changed:
        tmp = pos_file + ".tmp"
        with open(tmp, "w") as f:
            json.dump(all_pos, f, indent=2)
        os.replace(tmp, pos_file)
        logger.info("Reconcile: positions.json updated")


LAST_TICK_FILE = "data/last_tick.json"


def _record_tick():
    """Record the current time as the last successful tick."""
    os.makedirs("data", exist_ok=True)
    with open(LAST_TICK_FILE, "w") as f:
        json.dump({"ts": datetime.now(timezone.utc).isoformat()}, f)


def main():
    validate()

    w3 = _connect_rpc()

    # Global startup: ensure all positions have cg_id for price tracking
    _backfill_position_cg_ids()
    # Remove ghost positions where wallet balance is confirmed zero on-chain
    _reconcile_positions(w3)

    wallet = Wallet(w3)
    market = Market(w3)
    portfolio = Portfolio(w3, wallet, market)
    executor = Executor(w3, wallet)
    agent = TradingAgent(portfolio, executor)

    logger.info("Trading agent started — interval adapts to performance tier")

    def run_and_reschedule():
        """Run one agent tick, then reschedule based on current performance tier."""
        prices = Market().get_all_prices()
        tier   = get_tier(prices)
        agent.current_tier = tier
        agent.run_once()
        # Tick succeeded — record timestamp and clear any credit alert
        _record_tick()
        _alert_file = "data/credit_alert.json"
        if os.path.exists(_alert_file):
            os.remove(_alert_file)
        schedule.clear()
        schedule.every(tier["interval_seconds"]).seconds.do(run_and_reschedule)
        logger.info(f"Next tick in {tier['interval_seconds']//60}min (tier: {tier['label']})")

    # First run immediately
    tier = get_tier(Market().get_all_prices())
    agent.current_tier = tier
    agent.run_once()
    _record_tick()

    schedule.every(tier["interval_seconds"]).seconds.do(run_and_reschedule)

    while True:
        schedule.run_pending()
        time.sleep(1)


if __name__ == "__main__":
    RESTART_DELAY = 30
    while True:
        try:
            main()
        except SystemExit:
            break
        except KeyboardInterrupt:
            logger.info("Bot stopped by user.")
            break
        except Exception as e:
            logger.error(f"Bot crashed: {e} — restarting in {RESTART_DELAY}s...")
            # Flag credit exhaustion so dashboard can alert the user
            if "credit balance is too low" in str(e):
                os.makedirs("data", exist_ok=True)
                with open("data/credit_alert.json", "w") as _f:
                    json.dump({"ts": datetime.now(timezone.utc).isoformat(), "active": True}, _f)
            time.sleep(RESTART_DELAY)
