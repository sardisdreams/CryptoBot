import time
from web3 import Web3
from bot.config import (
    AERODROME_ROUTER, AERODROME_FACTORY,
    SLIPPAGE_TOLERANCE, SLIPPAGE_TOLERANCE_LOWCAP, SLIPPAGE_MAX, HIGH_LIQUIDITY_TOKENS,
    GAS_LIMIT, BASE_CHAIN_ID, WETH_ADDRESS,
)
from bot.logger import setup_logger

logger = setup_logger("aerodrome")

ROUTER_ABI = [
    {
        "inputs": [
            {"name": "amountIn", "type": "uint256"},
            {"components": [
                {"name": "from",    "type": "address"},
                {"name": "to",      "type": "address"},
                {"name": "stable",  "type": "bool"},
                {"name": "factory", "type": "address"},
            ], "name": "routes", "type": "tuple[]"},
        ],
        "name": "getAmountsOut",
        "outputs": [{"name": "amounts", "type": "uint256[]"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"name": "amountIn",    "type": "uint256"},
            {"name": "amountOutMin","type": "uint256"},
            {"components": [
                {"name": "from",    "type": "address"},
                {"name": "to",      "type": "address"},
                {"name": "stable",  "type": "bool"},
                {"name": "factory", "type": "address"},
            ], "name": "routes", "type": "tuple[]"},
            {"name": "to",       "type": "address"},
            {"name": "deadline", "type": "uint256"},
        ],
        "name": "swapExactTokensForTokens",
        "outputs": [{"name": "amounts", "type": "uint256[]"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"name": "amountIn",    "type": "uint256"},
            {"name": "amountOutMin","type": "uint256"},
            {"components": [
                {"name": "from",    "type": "address"},
                {"name": "to",      "type": "address"},
                {"name": "stable",  "type": "bool"},
                {"name": "factory", "type": "address"},
            ], "name": "routes", "type": "tuple[]"},
            {"name": "to",       "type": "address"},
            {"name": "deadline", "type": "uint256"},
        ],
        "name": "swapExactETHForTokens",
        "outputs": [{"name": "amounts", "type": "uint256[]"}],
        "stateMutability": "payable",
        "type": "function",
    },
    {
        "inputs": [
            {"name": "amountIn",    "type": "uint256"},
            {"name": "amountOutMin","type": "uint256"},
            {"components": [
                {"name": "from",    "type": "address"},
                {"name": "to",      "type": "address"},
                {"name": "stable",  "type": "bool"},
                {"name": "factory", "type": "address"},
            ], "name": "routes", "type": "tuple[]"},
            {"name": "to",       "type": "address"},
            {"name": "deadline", "type": "uint256"},
        ],
        "name": "swapExactTokensForETH",
        "outputs": [{"name": "amounts", "type": "uint256[]"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
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


class AerodromeRouter:
    def __init__(self, w3: Web3, wallet):
        self.w3 = w3
        self.wallet = wallet
        self.router = w3.eth.contract(
            address=Web3.to_checksum_address(AERODROME_ROUTER),
            abi=ROUTER_ABI,
        )
        self.factory = Web3.to_checksum_address(AERODROME_FACTORY)

    def _build_routes(self, token_in: str, token_out: str) -> list[list[dict]]:
        """
        Build candidate route sets to try — direct volatile, direct stable,
        and two-hop via WETH for tokens without a direct pool.
        """
        tin  = Web3.to_checksum_address(token_in)
        tout = Web3.to_checksum_address(token_out)
        weth = Web3.to_checksum_address(WETH_ADDRESS)

        return [
            # Direct volatile
            [{"from": tin, "to": tout, "stable": False, "factory": self.factory}],
            # Direct stable (for stable-stable pairs)
            [{"from": tin, "to": tout, "stable": True,  "factory": self.factory}],
            # Two-hop via WETH (volatile both legs)
            [
                {"from": tin,  "to": weth, "stable": False, "factory": self.factory},
                {"from": weth, "to": tout, "stable": False, "factory": self.factory},
            ],
        ]

    def get_quote(self, token_in: str, token_out: str, amount_in_wei: int) -> tuple[int, list[dict]]:
        """
        Returns (best_amount_out, best_routes).
        Tries direct volatile, direct stable, then two-hop via WETH.
        """
        best_out    = 0
        best_routes = []

        for routes in self._build_routes(token_in, token_out):
            try:
                amounts = self.router.functions.getAmountsOut(amount_in_wei, routes).call()
                out = amounts[-1]
                if out > best_out:
                    best_out    = out
                    best_routes = routes
            except Exception:
                continue

        if best_out > 0:
            logger.info(f"Aerodrome quote: {amount_in_wei} → {best_out} via {len(best_routes)}-hop")
        return best_out, best_routes

    def _ensure_approval(self, token_address: str, amount_wei: int):
        token = self.w3.eth.contract(
            address=Web3.to_checksum_address(token_address), abi=ERC20_ABI
        )
        allowance = token.functions.allowance(
            self.wallet.address, Web3.to_checksum_address(AERODROME_ROUTER)
        ).call()
        if allowance >= amount_wei:
            return
        logger.info(f"Approving Aerodrome router for {token_address}")
        gas_price = self.w3.eth.gas_price
        tx = token.functions.approve(
            Web3.to_checksum_address(AERODROME_ROUTER), amount_wei  # exact amount — no unlimited approval
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
        token_in: str,
        token_out: str,
        amount_in_wei: int,
        routes: list[dict],
        amount_out: int,
        slippage: float | None = None,
    ) -> str | None:
        # Caller passes slippage explicitly for sells (15%) vs buys (3%).
        # Do NOT derive from token symbols here — token_in/out are addresses, not symbols.
        if slippage is not None:
            slip = slippage
        else:
            slip = min(SLIPPAGE_TOLERANCE_LOWCAP, SLIPPAGE_MAX)
        min_out   = int(amount_out * (1 - slip))
        deadline  = int(time.time()) + 300
        gas_price = self.w3.eth.gas_price

        # WETH on Base is an ERC-20 (the bot always wraps native ETH via wrap_eth()
        # before swapping). All swaps go through swapExactTokensForTokens with prior
        # ERC-20 approval — never through the native ETH path.
        self._ensure_approval(token_in, amount_in_wei)

        try:
            tx = self.router.functions.swapExactTokensForTokens(
                amount_in_wei, min_out, routes, self.wallet.address, deadline
            ).build_transaction({
                "from": self.wallet.address,
                "value": 0,
                "gas": GAS_LIMIT,
                "gasPrice": gas_price,
                "nonce": self.wallet.get_nonce(),
                "chainId": BASE_CHAIN_ID,
            })

            logger.info(f"Aerodrome swap: {amount_in_wei} {token_in} -> {token_out}")
            return self.wallet.sign_and_send(tx)

        except Exception as e:
            logger.error(f"Aerodrome swap failed: {e}")
            return None
