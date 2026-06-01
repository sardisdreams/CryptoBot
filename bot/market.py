from web3 import Web3
from bot.config import UNISWAP_V3_QUOTER, USDC_ADDRESS, WETH_ADDRESS, TOKENS, STABLECOINS, DEFAULT_FEE
from bot.logger import setup_logger

logger = setup_logger("market")

QUOTER_ABI = [
    {
        "inputs": [
            {"name": "tokenIn", "type": "address"},
            {"name": "tokenOut", "type": "address"},
            {"name": "fee", "type": "uint24"},
            {"name": "amountIn", "type": "uint256"},
            {"name": "sqrtPriceLimitX96", "type": "uint160"},
        ],
        "name": "quoteExactInputSingle",
        "outputs": [{"name": "amountOut", "type": "uint256"}],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]


class Market:
    def __init__(self, w3: Web3):
        self.w3 = w3
        self.quoter = w3.eth.contract(
            address=Web3.to_checksum_address(UNISWAP_V3_QUOTER),
            abi=QUOTER_ABI,
        )

    def get_price_usd(self, symbol: str) -> float:
        """Return the USD price of any token in the registry."""
        if symbol in STABLECOINS:
            return 1.0

        token = TOKENS.get(symbol)
        if not token:
            logger.warning(f"Unknown token: {symbol}")
            return 0.0

        try:
            amount_in = 10 ** token["decimals"]
            usdc_out = self.quoter.functions.quoteExactInputSingle(
                Web3.to_checksum_address(token["address"]),
                Web3.to_checksum_address(USDC_ADDRESS),
                DEFAULT_FEE,
                amount_in,
                0,
            ).call()
            return usdc_out / 1e6
        except Exception:
            # Fallback: route through WETH then USDC
            try:
                eth_out = self.quoter.functions.quoteExactInputSingle(
                    Web3.to_checksum_address(token["address"]),
                    Web3.to_checksum_address(WETH_ADDRESS),
                    DEFAULT_FEE,
                    10 ** token["decimals"],
                    0,
                ).call()
                eth_price = self._get_eth_price_usd()
                return (eth_out / 1e18) * eth_price
            except Exception as e:
                logger.error(f"Price fetch failed for {symbol}: {e}")
                return 0.0

    def _get_eth_price_usd(self) -> float:
        try:
            usdc_out = self.quoter.functions.quoteExactInputSingle(
                Web3.to_checksum_address(WETH_ADDRESS),
                Web3.to_checksum_address(USDC_ADDRESS),
                DEFAULT_FEE,
                10 ** 18,
                0,
            ).call()
            return usdc_out / 1e6
        except Exception as e:
            logger.error(f"ETH price fetch failed: {e}")
            return 0.0

    def get_all_prices(self) -> dict[str, float]:
        """Return USD prices for all tokens in the registry."""
        prices = {}
        eth_price = self._get_eth_price_usd()
        prices["WETH"] = eth_price
        for symbol in TOKENS:
            if symbol == "WETH":
                continue
            prices[symbol] = self.get_price_usd(symbol)
        logger.info(f"Prices fetched: { {k: f'${v:,.2f}' for k, v in prices.items()} }")
        return prices
