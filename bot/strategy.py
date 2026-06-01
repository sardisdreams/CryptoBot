from web3 import Web3
from bot.config import WETH, USDC, MAX_TRADE_SIZE_ETH
from bot.dex import DEX
from bot.wallet import Wallet
from bot.logger import setup_logger

logger = setup_logger("strategy")

# Simple moving-average crossover strategy scaffold.
# Replace with your own signal logic.


class SimpleMAStrategy:
    def __init__(self, w3: Web3, wallet: Wallet, dex: DEX):
        self.w3 = w3
        self.wallet = wallet
        self.dex = dex
        self.prices: list[float] = []
        self.short_window = 5
        self.long_window = 20

    def record_price(self, price: float):
        self.prices.append(price)
        if len(self.prices) > self.long_window * 2:
            self.prices = self.prices[-(self.long_window * 2):]

    def _sma(self, window: int) -> float | None:
        if len(self.prices) < window:
            return None
        return sum(self.prices[-window:]) / window

    def get_signal(self) -> str:
        short = self._sma(self.short_window)
        long = self._sma(self.long_window)
        if short is None or long is None:
            return "hold"
        if short > long:
            return "buy"
        if short < long:
            return "sell"
        return "hold"

    def get_current_price(self) -> float:
        # Price of 1 ETH in USDC (6 decimals)
        one_eth = self.w3.to_wei(1, "ether")
        usdc_out = self.dex.get_quote(WETH, USDC, one_eth)
        return usdc_out / 1e6

    def run_once(self):
        price = self.get_current_price()
        if price == 0:
            logger.warning("Could not fetch price — skipping tick")
            return

        self.record_price(price)
        signal = self.get_signal()
        eth_balance = self.wallet.get_eth_balance()
        usdc_balance = self.wallet.get_token_balance(USDC, decimals=6)

        logger.info(
            f"Price: ${price:.2f} | Signal: {signal} | "
            f"ETH: {eth_balance:.4f} | USDC: {usdc_balance:.2f}"
        )

        trade_eth = min(float(MAX_TRADE_SIZE_ETH), eth_balance * 0.95)
        amount_in = self.w3.to_wei(trade_eth, "ether")

        if signal == "buy" and eth_balance > trade_eth:
            logger.info(f"BUY signal — swapping {trade_eth} ETH → USDC")
            self.dex.swap_exact_input(WETH, USDC, amount_in)

        elif signal == "sell" and usdc_balance > 1:
            trade_usdc = min(usdc_balance * 0.95, usdc_balance)
            amount_usdc = int(trade_usdc * 1e6)
            logger.info(f"SELL signal — swapping {trade_usdc:.2f} USDC → ETH")
            self.dex.swap_exact_input(USDC, WETH, amount_usdc)
