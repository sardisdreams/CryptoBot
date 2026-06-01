from web3 import Web3
from bot.config import (
    UNISWAP_V3_ROUTER, UNISWAP_V3_QUOTER,
    SLIPPAGE_TOLERANCE, GAS_LIMIT, BASE_CHAIN_ID,
)
from bot.wallet import Wallet
from bot.logger import setup_logger
from bot import recorder
import time

logger = setup_logger("dex")

# Minimal Uniswap V3 ABIs
ROUTER_ABI = [
    {
        "inputs": [{
            "components": [
                {"name": "tokenIn", "type": "address"},
                {"name": "tokenOut", "type": "address"},
                {"name": "fee", "type": "uint24"},
                {"name": "recipient", "type": "address"},
                {"name": "deadline", "type": "uint256"},
                {"name": "amountIn", "type": "uint256"},
                {"name": "amountOutMinimum", "type": "uint256"},
                {"name": "sqrtPriceLimitX96", "type": "uint160"},
            ],
            "name": "params", "type": "tuple",
        }],
        "name": "exactInputSingle",
        "outputs": [{"name": "amountOut", "type": "uint256"}],
        "stateMutability": "payable",
        "type": "function",
    }
]

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

# Pool fee tiers: 500 = 0.05%, 3000 = 0.3%, 10000 = 1%
DEFAULT_FEE = 3000


class DEX:
    def __init__(self, w3: Web3, wallet: Wallet):
        self.w3 = w3
        self.wallet = wallet
        self.router = w3.eth.contract(
            address=Web3.to_checksum_address(UNISWAP_V3_ROUTER),
            abi=ROUTER_ABI,
        )
        self.quoter = w3.eth.contract(
            address=Web3.to_checksum_address(UNISWAP_V3_QUOTER),
            abi=QUOTER_ABI,
        )

    def get_quote(self, token_in: str, token_out: str, amount_in_wei: int, fee: int = DEFAULT_FEE) -> int:
        try:
            amount_out = self.quoter.functions.quoteExactInputSingle(
                Web3.to_checksum_address(token_in),
                Web3.to_checksum_address(token_out),
                fee,
                amount_in_wei,
                0,
            ).call()
            return amount_out
        except Exception as e:
            logger.error(f"Quote failed: {e}")
            return 0

    def swap_exact_input(
        self,
        token_in: str,
        token_out: str,
        amount_in_wei: int,
        fee: int = DEFAULT_FEE,
    ) -> str | None:
        amount_out = self.get_quote(token_in, token_out, amount_in_wei, fee)
        if amount_out == 0:
            logger.warning("Swap skipped: quote returned 0")
            return None

        min_out = int(amount_out * (1 - SLIPPAGE_TOLERANCE))
        deadline = int(time.time()) + 300  # 5 min

        gas_price = self.w3.eth.gas_price
        tx = self.router.functions.exactInputSingle({
            "tokenIn": Web3.to_checksum_address(token_in),
            "tokenOut": Web3.to_checksum_address(token_out),
            "fee": fee,
            "recipient": self.wallet.address,
            "deadline": deadline,
            "amountIn": amount_in_wei,
            "amountOutMinimum": min_out,
            "sqrtPriceLimitX96": 0,
        }).build_transaction({
            "from": self.wallet.address,
            "value": amount_in_wei if token_in.lower() == "0x4200000000000000000000000000000000000006" else 0,
            "gas": GAS_LIMIT,
            "gasPrice": gas_price,
            "nonce": self.wallet.get_nonce(),
            "chainId": BASE_CHAIN_ID,
        })

        logger.info(f"Swapping {amount_in_wei} of {token_in} → {token_out} (min out: {min_out})")
        tx_hash = self.wallet.sign_and_send(tx)

        gas_price_gwei = self.w3.from_wei(gas_price, "gwei")
        recorder.record_transaction(
            tx_hash=tx_hash,
            token_in=token_in,
            amount_in=amount_in_wei / 1e18,
            token_out=token_out,
            amount_out=amount_out,
            gas_price_gwei=float(gas_price_gwei),
            status="pending",
        )

        try:
            receipt = self.wallet.wait_for_receipt(tx_hash)
            status = "success" if receipt["status"] == 1 else "failed"
            recorder.update_status(tx_hash, status, gas_used=receipt["gasUsed"])
        except Exception as e:
            logger.warning(f"Could not confirm receipt for {tx_hash}: {e}")
            recorder.update_status(tx_hash, "unknown")

        return tx_hash
