import time
from web3 import Web3
from bot.config import (
    UNISWAP_V3_ROUTER, UNISWAP_V3_QUOTER,
    SLIPPAGE_TOLERANCE, GAS_LIMIT, BASE_CHAIN_ID, DEFAULT_FEE, WETH_ADDRESS,
)
from bot.wallet import Wallet
from bot.logger import setup_logger
from bot import recorder

logger = setup_logger("executor")

ROUTER_ABI = [
    {
        "inputs": [{"components": [
            {"name": "tokenIn", "type": "address"},
            {"name": "tokenOut", "type": "address"},
            {"name": "fee", "type": "uint24"},
            {"name": "recipient", "type": "address"},
            {"name": "deadline", "type": "uint256"},
            {"name": "amountIn", "type": "uint256"},
            {"name": "amountOutMinimum", "type": "uint256"},
            {"name": "sqrtPriceLimitX96", "type": "uint160"},
        ], "name": "params", "type": "tuple"}],
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

ERC20_ABI = [
    {"inputs": [{"name": "account", "type": "address"}],
     "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}],
     "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
     "name": "approve", "outputs": [{"name": "", "type": "bool"}],
     "stateMutability": "nonpayable", "type": "function"},
    {"inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
     "name": "allowance", "outputs": [{"name": "", "type": "uint256"}],
     "stateMutability": "view", "type": "function"},
]


class Executor:
    def __init__(self, w3: Web3, wallet: Wallet):
        self.w3 = w3
        self.wallet = wallet
        self.router = w3.eth.contract(
            address=Web3.to_checksum_address(UNISWAP_V3_ROUTER), abi=ROUTER_ABI
        )
        self.quoter = w3.eth.contract(
            address=Web3.to_checksum_address(UNISWAP_V3_QUOTER), abi=QUOTER_ABI
        )

    def get_quote(self, token_in: str, token_out: str, amount_in_wei: int, fee: int = DEFAULT_FEE) -> int:
        try:
            return self.quoter.functions.quoteExactInputSingle(
                Web3.to_checksum_address(token_in),
                Web3.to_checksum_address(token_out),
                fee,
                amount_in_wei,
                0,
            ).call()
        except Exception as e:
            logger.error(f"Quote failed: {e}")
            return 0

    def _ensure_approval(self, token_address: str, amount_wei: int):
        """Approve the router to spend ERC-20 tokens if allowance is insufficient."""
        token = self.w3.eth.contract(
            address=Web3.to_checksum_address(token_address), abi=ERC20_ABI
        )
        allowance = token.functions.allowance(
            self.wallet.address, Web3.to_checksum_address(UNISWAP_V3_ROUTER)
        ).call()

        if allowance >= amount_wei:
            return

        logger.info(f"Approving router for token {token_address}")
        gas_price = self.w3.eth.gas_price
        tx = token.functions.approve(
            Web3.to_checksum_address(UNISWAP_V3_ROUTER),
            2**256 - 1,  # max approval
        ).build_transaction({
            "from": self.wallet.address,
            "gas": 100_000,
            "gasPrice": gas_price,
            "nonce": self.wallet.get_nonce(),
            "chainId": BASE_CHAIN_ID,
        })
        tx_hash = self.wallet.sign_and_send(tx)
        self.wallet.wait_for_receipt(tx_hash)

    def swap(
        self,
        token_in_address: str,
        token_in_symbol: str,
        token_in_decimals: int,
        token_out_address: str,
        token_out_symbol: str,
        amount_in_wei: int,
        price_eth_usd: float = 0.0,
        fee: int = DEFAULT_FEE,
    ) -> str | None:
        amount_out = self.get_quote(token_in_address, token_out_address, amount_in_wei, fee)
        if amount_out == 0:
            logger.warning("Swap skipped: quote returned 0")
            return None

        is_native_eth = token_in_address.lower() == WETH_ADDRESS.lower()
        if not is_native_eth:
            self._ensure_approval(token_in_address, amount_in_wei)

        min_out = int(amount_out * (1 - SLIPPAGE_TOLERANCE))
        deadline = int(time.time()) + 300
        gas_price = self.w3.eth.gas_price

        tx = self.router.functions.exactInputSingle({
            "tokenIn": Web3.to_checksum_address(token_in_address),
            "tokenOut": Web3.to_checksum_address(token_out_address),
            "fee": fee,
            "recipient": self.wallet.address,
            "deadline": deadline,
            "amountIn": amount_in_wei,
            "amountOutMinimum": min_out,
            "sqrtPriceLimitX96": 0,
        }).build_transaction({
            "from": self.wallet.address,
            "value": amount_in_wei if is_native_eth else 0,
            "gas": GAS_LIMIT,
            "gasPrice": gas_price,
            "nonce": self.wallet.get_nonce(),
            "chainId": BASE_CHAIN_ID,
        })

        logger.info(f"Swapping {amount_in_wei / 10**token_in_decimals:.6f} {token_in_symbol} → {token_out_symbol}")
        tx_hash = self.wallet.sign_and_send(tx)

        recorder.record_transaction(
            tx_hash=tx_hash,
            token_in=token_in_symbol,
            amount_in=amount_in_wei / 10 ** token_in_decimals,
            token_out=token_out_symbol,
            amount_out=amount_out,
            price_eth_usd=price_eth_usd,
            gas_price_gwei=float(self.w3.from_wei(gas_price, "gwei")),
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
