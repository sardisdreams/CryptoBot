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
