"""
Hyperliquid perpetuals bot — main entry point.

Runs independently from the Base bot (separate process, separate wallet).
Deploy as a separate systemd service (deploy/hl_cryptobot.service).
"""
import bot.ssl_fix  # must be first — patches SSL before any HTTPS calls

import os
import time
import schedule
from datetime import datetime, timezone
from dotenv import load_dotenv

from bot.logger import setup_logger
from hyperliquid.config import validate, TICK_INTERVAL_SECONDS, HL_BOT_VERSION
from hyperliquid.agent import HLAgent
from hyperliquid import executor, market

load_dotenv()
logger = setup_logger("hl.main")

os.makedirs("logs", exist_ok=True)
os.makedirs("records", exist_ok=True)
os.makedirs("data", exist_ok=True)

LAST_TICK_FILE = "data/hl_last_tick.json"


def _record_tick():
    import json
    with open(LAST_TICK_FILE, "w") as f:
        json.dump({"ts": datetime.now(timezone.utc).isoformat()}, f)


def main():
    validate()

    logger.info(f"Hyperliquid bot {HL_BOT_VERSION} starting")

    # Derive wallet address from private key (no RPC needed for HL)
    from eth_account import Account
    from hyperliquid.config import HL_PRIVATE_KEY
    address = Account.from_key(HL_PRIVATE_KEY).address
    logger.info(f"Wallet: {address}")

    # Startup: reconcile local position records against Hyperliquid on-chain state
    try:
        executor.reconcile_positions(address)
    except Exception as e:
        logger.warning(f"Startup reconcile failed (non-fatal): {e}")

    agent = HLAgent()

    def run_tick():
        try:
            agent.run_once()
            _record_tick()
        except Exception as e:
            logger.error(f"HL tick error: {e}")

        # Reschedule
        schedule.clear()
        schedule.every(TICK_INTERVAL_SECONDS).seconds.do(run_tick)
        logger.info(f"Next HL tick in {TICK_INTERVAL_SECONDS // 60}min")

    # First tick immediately
    run_tick()

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
            logger.info("HL bot stopped by user")
            break
        except Exception as e:
            logger.error(f"HL bot crashed: {e} — restarting in {RESTART_DELAY}s")
            time.sleep(RESTART_DELAY)
