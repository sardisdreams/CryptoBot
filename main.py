import os
import time
import schedule
from web3 import Web3
from dotenv import load_dotenv

from bot.config import BASE_RPC_URL, validate
from bot.logger import setup_logger
from bot.wallet import Wallet
from bot.dex import DEX
from bot.strategy import SimpleMAStrategy

load_dotenv()
logger = setup_logger("main")

os.makedirs("logs", exist_ok=True)


def main():
    validate()

    w3 = Web3(Web3.HTTPProvider(BASE_RPC_URL))
    if not w3.is_connected():
        logger.error(f"Cannot connect to Base RPC: {BASE_RPC_URL}")
        raise SystemExit(1)

    logger.info(f"Connected to Base chain (block {w3.eth.block_number})")

    wallet = Wallet(w3)
    dex = DEX(w3, wallet)
    strategy = SimpleMAStrategy(w3, wallet, dex)

    logger.info("Bot started — running every 60 seconds")
    schedule.every(60).seconds.do(strategy.run_once)

    # Run immediately on start
    strategy.run_once()

    while True:
        schedule.run_pending()
        time.sleep(1)


if __name__ == "__main__":
    main()
