from web3 import Web3
from bot.config import TOKENS, STABLECOINS
from bot.market import Market
from bot.wallet import Wallet
from bot.logger import setup_logger

logger = setup_logger("portfolio")

ERC20_BALANCE_ABI = [
    {"inputs": [{"name": "account", "type": "address"}],
     "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}],
     "stateMutability": "view", "type": "function"},
]


class Portfolio:
    def __init__(self, w3: Web3, wallet: Wallet, market: Market = None):
        self.w3 = w3
        self.wallet = wallet
        self.market = market or Market()

    def get_snapshot(self) -> dict:
        """Return full portfolio: balances, USD values, total value, allocations."""
        prices = self.market.get_all_prices()
        holdings = {}
        total_usd = 0.0

        # Native ETH balance
        eth_balance = float(self.wallet.get_eth_balance())
        eth_price = prices.get("WETH", 0.0)
        eth_usd = eth_balance * eth_price
        holdings["ETH"] = {
            "balance": eth_balance,
            "price_usd": eth_price,
            "value_usd": eth_usd,
            "address": TOKENS["WETH"]["address"],
            "decimals": 18,
        }
        total_usd += eth_usd

        # ERC-20 tokens
        for symbol, info in TOKENS.items():
            if symbol == "WETH":
                continue
            try:
                contract = self.w3.eth.contract(
                    address=Web3.to_checksum_address(info["address"]),
                    abi=ERC20_BALANCE_ABI,
                )
                raw = contract.functions.balanceOf(self.wallet.address).call()
                balance = raw / (10 ** info["decimals"])
                price = prices.get(symbol, 0.0)
                value_usd = balance * price
                holdings[symbol] = {
                    "balance": balance,
                    "price_usd": price,
                    "value_usd": value_usd,
                    "address": info["address"],
                    "decimals": info["decimals"],
                }
                total_usd += value_usd
            except Exception as e:
                logger.warning(f"Could not fetch balance for {symbol}: {e}")

        # Allocation percentages
        for symbol in holdings:
            holdings[symbol]["allocation_pct"] = (
                (holdings[symbol]["value_usd"] / total_usd * 100) if total_usd > 0 else 0.0
            )

        snapshot = {
            "holdings": holdings,
            "total_usd": total_usd,
            "prices": prices,
        }

        logger.info(f"Portfolio total: ${total_usd:,.2f}")
        for symbol, h in holdings.items():
            if h["balance"] > 0:
                logger.info(f"  {symbol}: {h['balance']:.6f} (${h['value_usd']:,.2f} | {h['allocation_pct']:.1f}%)")

        return snapshot

    def max_trade_size_usd(self, risk_pct: float = 0.05) -> float:
        """Return max trade size in USD as a percentage of total portfolio value."""
        snapshot = self.get_snapshot()
        return snapshot["total_usd"] * risk_pct
