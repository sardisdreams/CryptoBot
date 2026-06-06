import time
from web3 import Web3
from bot.config import (
    UNISWAP_V3_ROUTER, UNISWAP_V3_QUOTER,
    SLIPPAGE_TOLERANCE, SLIPPAGE_TOLERANCE_LOWCAP, SLIPPAGE_MAX, MAX_PRICE_IMPACT,
    HIGH_LIQUIDITY_TOKENS, GAS_LIMIT, BASE_CHAIN_ID, DEFAULT_FEE, WETH_ADDRESS, STABLECOINS,
)
from bot.wallet import Wallet
from bot.logger import setup_logger
from bot import recorder, positions, knowledge
from bot.aerodrome import AerodromeRouter
from bot.cost_tracker import record_gas
from bot import capital

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
        "inputs": [{"components": [
            {"name": "tokenIn", "type": "address"},
            {"name": "tokenOut", "type": "address"},
            {"name": "amountIn", "type": "uint256"},
            {"name": "fee", "type": "uint24"},
            {"name": "sqrtPriceLimitX96", "type": "uint160"},
        ], "name": "params", "type": "tuple"}],
        "name": "quoteExactInputSingle",
        "outputs": [
            {"name": "amountOut", "type": "uint256"},
            {"name": "sqrtPriceX96After", "type": "uint160"},
            {"name": "initializedTicksCrossed", "type": "uint32"},
            {"name": "gasEstimate", "type": "uint256"},
        ],
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


def _ensure_wiki_entry(symbol: str, cg_id: str, contract_address: str, price_usd: float):
    """
    Auto-generate a basic wiki entry for any token we buy if one doesn't exist.
    Ensures the bot always has structured knowledge about every position it holds.
    """
    import os
    wiki_path = os.path.join("wiki", f"{symbol.upper()}.md")
    if os.path.exists(wiki_path):
        return  # already has a wiki entry
    from bot import token_cache as tc
    cached = tc.get(cg_id) if cg_id else tc.get_by_symbol(symbol)
    name = cached.get("name", symbol) if cached else symbol
    content = f"""---
symbol: {symbol.upper()}
name: {name}
chain: Base
contract: "{contract_address}"
cg_id: {cg_id or "unknown"}
decimals: 18
type: Unknown
risk: Unknown
status: ACTIVE — auto-generated on first buy
---

# {name} ({symbol.upper()})

## What it is
Auto-generated entry. Update with project details when available.

## Trading notes
- First bought at ${price_usd:.6f}
- Use evaluate_coin('{cg_id}') to get a full safety assessment
- Monitor closely — limited prior knowledge about this token

## Risk factors
- Limited information available at time of first trade
- Verify contract legitimacy on BaseScan before adding to notes
"""
    os.makedirs("wiki", exist_ok=True)
    with open(wiki_path, "w") as f:
        f.write(content)
    logger.info(f"Auto-generated wiki entry for {symbol}")


def _record_trade_postmortem(record: dict, exit_reasoning: str):
    """Write a trade post-mortem to the knowledge base after any close."""
    token       = record.get("token", "?")
    gain_pct    = record.get("gain_loss_pct", 0)
    gain_usd    = record.get("gain_loss_usd", 0)
    cost        = record.get("cost_basis_usd", 0)
    hold_days   = record.get("hold_days", 0)
    entry_rsn   = record.get("entry_reasoning", "") or "no entry reasoning recorded"
    exit_rsn    = exit_reasoning or "no exit reasoning recorded"
    outcome     = "WIN" if gain_pct >= 0 else "LOSS"

    hold_str = f"{hold_days}d" if hold_days >= 1 else "<1d"
    summary = (
        f"{outcome} | {token} {gain_pct:+.1f}% (${gain_usd:+.2f} on ${cost:.0f}) | "
        f"held {hold_str} | "
        f"ENTRY: {entry_rsn} | "
        f"EXIT: {exit_rsn}"
    )
    cat = "strategy"
    knowledge.add_entry(cat, summary)
    logger.info(f"Trade post-mortem saved to knowledge base: {token} {gain_pct:+.1f}%")

    # Lock 10% of any profit into the floor
    if gain_usd > 0:
        capital.lock_profit(gain_usd)
        logger.info(f"Profit lock: +${gain_usd * 0.10:.2f} added to floor (floor now ${capital.get_floor():.2f})")


class Executor:
    def __init__(self, w3: Web3, wallet: Wallet):
        self.w3 = w3
        self.wallet = wallet
        self.aerodrome = AerodromeRouter(w3, wallet)
        self.router = w3.eth.contract(
            address=Web3.to_checksum_address(UNISWAP_V3_ROUTER), abi=ROUTER_ABI
        )
        self.quoter = w3.eth.contract(
            address=Web3.to_checksum_address(UNISWAP_V3_QUOTER), abi=QUOTER_ABI
        )

    def get_quote(self, token_in: str, token_out: str, amount_in_wei: int, fee: int = DEFAULT_FEE) -> int:
        # Try the requested fee tier first, then fall back to others
        fee_tiers = [fee] + [f for f in [500, 3000, 10000] if f != fee]
        for f in fee_tiers:
            try:
                result = self.quoter.functions.quoteExactInputSingle({
                    "tokenIn": Web3.to_checksum_address(token_in),
                    "tokenOut": Web3.to_checksum_address(token_out),
                    "amountIn": amount_in_wei,
                    "fee": f,
                    "sqrtPriceLimitX96": 0,
                }).call({"from": self.wallet.address})
                if result[0] > 0:
                    if f != fee:
                        logger.info(f"Quote succeeded on fee tier {f}")
                    return result[0]
            except Exception:
                continue
        logger.error(f"Quote failed on all fee tiers for {token_in} -> {token_out}")
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
        token_in_price_usd: float = 0.0,
        token_out_price_usd: float = 0.0,
        take_profit_pct: float = 25.0,
        stop_loss_pct: float = 25.0,
        max_hold_hours: float = 48.0,
        fee: int = DEFAULT_FEE,
        entry_reasoning: str = "",
        exit_reasoning: str = "",
        cg_id: str = "",
    ) -> str | None:
        # Try Uniswap V3 first, fall back to Aerodrome
        dex_used = "uniswap_v3"
        amount_out = self.get_quote(token_in_address, token_out_address, amount_in_wei, fee)
        aerodrome_routes = None

        if amount_out == 0:
            logger.info("Uniswap V3 quote = 0, trying Aerodrome...")
            amount_out, aerodrome_routes = self.aerodrome.get_quote(
                token_in_address, token_out_address, amount_in_wei
            )
            if amount_out > 0:
                dex_used = "aerodrome"

        if amount_out == 0:
            logger.warning("Swap skipped: no liquidity on Uniswap V3 or Aerodrome")
            return None

        # Price impact check — protect against near-zero liquidity pools
        amount_in_usd = (amount_in_wei / 10 ** token_in_decimals) * token_in_price_usd if token_in_price_usd > 0 else 0

        # Block buys when we have no reference price for the token we're buying
        is_buy = token_in_symbol in STABLECOINS and token_out_symbol not in STABLECOINS
        if is_buy and token_out_price_usd <= 0:
            logger.warning(
                f"Swap rejected: no reference price for {token_out_symbol}. "
                f"Cannot verify execution quality without a market price."
            )
            return None

        if token_in_price_usd > 0 and token_out_price_usd > 0:
            # Infer token_out decimals from amount
            for dec in [18, 8, 6]:
                amount_out_tokens = amount_out / (10 ** dec)
                amount_out_usd    = amount_out_tokens * token_out_price_usd
                if amount_out_usd > 0.01:
                    break

            # Standard price impact: are we getting fair value out?
            if amount_in_usd > 0:
                price_impact = (amount_in_usd - amount_out_usd) / amount_in_usd
                logger.info(f"Price impact: {price_impact:.1%} (in ${amount_in_usd:.2f} → out ${amount_out_usd:.2f})")
                if price_impact > MAX_PRICE_IMPACT:
                    logger.warning(
                        f"Swap rejected: price impact {price_impact:.1%} exceeds {MAX_PRICE_IMPACT:.0%} max. "
                        f"Token likely has insufficient liquidity on Base."
                    )
                    return None

            # Execution price check: compare on-chain implied price to CoinGecko reference.
            # Catches cases where DEX pool price is completely disconnected from market.
            if is_buy and amount_out_tokens > 0 and amount_in_usd > 0:
                implied_price = amount_in_usd / amount_out_tokens
                price_premium = (implied_price - token_out_price_usd) / token_out_price_usd
                logger.info(f"Execution price check: implied ${implied_price:.6f} vs reference ${token_out_price_usd:.6f} ({price_premium:+.1%})")
                if price_premium > 0.10:
                    logger.warning(
                        f"Swap rejected: on-chain price ${implied_price:.6f} is {price_premium:.1%} above "
                        f"CoinGecko reference ${token_out_price_usd:.6f} for {token_out_symbol}. "
                        f"Pool likely has near-zero liquidity on Base."
                    )
                    return None

        # Gas cost check — skip trade if gas > 2% of trade size
        if token_in_price_usd > 0:
            current_gas_price = self.w3.eth.gas_price
            gas_cost_eth = (GAS_LIMIT * current_gas_price) / 1e18
            eth_price = token_in_price_usd if token_in_symbol == "WETH" else token_out_price_usd if token_out_symbol == "WETH" else price_eth_usd
            gas_cost_usd = gas_cost_eth * eth_price if eth_price > 0 else 0
            trade_usd = (amount_in_wei / 10 ** token_in_decimals) * token_in_price_usd
            if gas_cost_usd > 0 and trade_usd > 0 and (gas_cost_usd / trade_usd) > 0.02:
                logger.warning(
                    f"Swap skipped: gas cost ${gas_cost_usd:.3f} is {gas_cost_usd/trade_usd:.1%} of trade "
                    f"${trade_usd:.2f} — exceeds 2% threshold"
                )
                return None

        logger.info(f"Executing on {dex_used}: {amount_in_wei / 10**token_in_decimals:.6f} {token_in_symbol} -> {token_out_symbol}")

        # Execute on the DEX that gave us a quote
        if dex_used == "aerodrome":
            tx_hash = self.aerodrome.swap(
                token_in_address, token_out_address,
                amount_in_wei, aerodrome_routes, amount_out,
            )
            if not tx_hash:
                return None
        else:
            is_native_eth = token_in_address.lower() == WETH_ADDRESS.lower()
            if not is_native_eth:
                self._ensure_approval(token_in_address, amount_in_wei)

            # Use tighter slippage for liquid tokens, wider for low-cap — capped at 5% max
            slip = SLIPPAGE_TOLERANCE if (token_in_symbol in HIGH_LIQUIDITY_TOKENS and token_out_symbol in HIGH_LIQUIDITY_TOKENS) else SLIPPAGE_TOLERANCE_LOWCAP
            slip = min(slip, SLIPPAGE_MAX)
            min_out   = int(amount_out * (1 - slip))
            deadline  = int(time.time()) + 300
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
            tx_hash = self.wallet.sign_and_send(tx)

        gas_price = self.w3.eth.gas_price
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
            gas_eth = (receipt["gasUsed"] * float(self.w3.from_wei(gas_price, "gwei"))) / 1e9
            record_gas(gas_eth, price_eth_usd)

            if status == "success":
                amount_in_tokens = amount_in_wei / 10 ** token_in_decimals
                amount_out_tokens = amount_out / 10 ** 6 if token_out_symbol in STABLECOINS else amount_out / 10 ** 18

                # Buying a non-stable token → open position
                if token_in_symbol in STABLECOINS and token_out_symbol not in STABLECOINS:
                    positions.open_position(
                        symbol=token_out_symbol,
                        amount_tokens=amount_out_tokens,
                        entry_price_usd=token_out_price_usd,
                        tx_hash=tx_hash,
                        take_profit_pct=take_profit_pct,
                        stop_loss_pct=stop_loss_pct,
                        max_hold_hours=max_hold_hours,
                        reasoning=entry_reasoning,
                        cg_id=cg_id,
                    )
                    logger.info(f"Position opened: {amount_out_tokens:.6f} {token_out_symbol} @ ${token_out_price_usd:.4f}")
                    # Auto-generate wiki entry for any new token we buy
                    _ensure_wiki_entry(token_out_symbol, cg_id, token_out_address, token_out_price_usd)

                # Selling a non-stable token → close position (FIFO)
                elif token_in_symbol not in STABLECOINS and token_out_symbol in STABLECOINS:
                    realized = positions.close_position(
                        symbol=token_in_symbol,
                        amount_tokens=amount_in_tokens,
                        exit_price_usd=token_in_price_usd,
                        exit_tx=tx_hash,
                    )
                    for r in realized:
                        logger.info(
                            f"Position closed: {r['amount_tokens']:.6f} {token_in_symbol} | "
                            f"P&L: ${r['gain_loss_usd']:+.2f} ({r['gain_loss_pct']:+.2f}%) | "
                            f"Held {r['hold_days']} days ({r['term']}-term)"
                        )
                        _record_trade_postmortem(r, exit_reasoning)

        except Exception as e:
            logger.warning(f"Could not confirm receipt for {tx_hash}: {e}")
            recorder.update_status(tx_hash, "unknown")

        return tx_hash
