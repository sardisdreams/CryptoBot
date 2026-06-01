import bot.ssl_fix  # must be first import — patches SSL before any HTTPS calls

import os
import time
import certifi
import schedule
import requests
from web3 import Web3
from dotenv import load_dotenv

from bot.config import BASE_RPC_URL, validate
from bot.logger import setup_logger
from bot.wallet import Wallet
from bot.market import Market
from bot.portfolio import Portfolio
from bot.executor import Executor
from bot.agent import TradingAgent

load_dotenv()
logger = setup_logger("main")

os.makedirs("logs", exist_ok=True)
os.makedirs("records", exist_ok=True)

RUN_INTERVAL_SECONDS = 3600  # 60 minutes — cost-saving mode (change to 1800 once profitable)


def main():
    validate()

    session = requests.Session()
    session.verify = certifi.where()
    w3 = Web3(Web3.HTTPProvider(BASE_RPC_URL, session=session))
    if not w3.is_connected():
        logger.error(f"Cannot connect to Base RPC: {BASE_RPC_URL}")
        raise SystemExit(1)

    logger.info(f"Connected to Base chain (block {w3.eth.block_number})")

    wallet = Wallet(w3)
    market = Market(w3)
    portfolio = Portfolio(w3, wallet, market)
    executor = Executor(w3, wallet)
    agent = TradingAgent(portfolio, executor)

    logger.info(f"Trading agent started — running every {RUN_INTERVAL_SECONDS}s")
    schedule.every(RUN_INTERVAL_SECONDS).seconds.do(agent.run_once)

    agent.run_once()

    while True:
        schedule.run_pending()
        time.sleep(1)


if __name__ == "__main__":
    RESTART_DELAY = 30  # seconds to wait before restarting after a crash
    while True:
        try:
            main()
        except SystemExit:
            break  # intentional exit — don't restart
        except KeyboardInterrupt:
            logger.info("Bot stopped by user.")
            break
        except Exception as e:
            logger.error(f"Bot crashed: {e} — restarting in {RESTART_DELAY}s...")
            time.sleep(RESTART_DELAY)
