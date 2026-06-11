import time
import os
import json
from datetime import datetime, timezone
from web3 import Web3
from bot.config import (
    UNISWAP_V3_ROUTER, UNISWAP_V3_QUOTER,
    SLIPPAGE_TOLERANCE, SLIPPAGE_TOLERANCE_LOWCAP, SLIPPAGE_MAX, MAX_PRICE_IMPACT,
    HIGH_LIQUIDITY_TOKENS, GAS_LIMIT, BASE_CHAIN_ID, DEFAULT_FEE, WETH_ADDRESS, STABLECOINS,
    TOKENS,
)
from bot.wallet import Wallet
from bot.logger import setup_logger
from bot import recorder, positions, knowledge
from bot.aerodrome import AerodromeRouter
from bot.cost_tracker import record_gas
from bot import capital, risk as risk_mod
from bot.thegraph import check_pool_liquidity
from bot.emailer import send_trade_notification

logger = setup_logger("executor")

_BLOCKS_FILE  = "data/trade_blocks.json"
_KEEP_BLOCKS  = 100


def _log_swap_block(token_in: str, token_out: str, amount_usd: float, reason: str):
    """Persist every executor-level swap rejection to the dashboard trade-issues feed."""
    os.makedirs("data", exist_ok=True)
    try:
        blocks = json.load(open(_BLOCKS_FILE)) if os.path.exists(_BLOCKS_FILE) else []
    except Exception:
        blocks = []
    blocks.append({
        "ts":         datetime.now(timezone.utc).isoformat(),
        "token_in":   token_in,
        "token_out":  token_out,
        "amount_usd": round(amount_usd, 2),
        "reason":     reason,
    })
    blocks = blocks[-_KEEP_BLOCKS:]
    try:
        tmp = _BLOCKS_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(blocks, f, indent=2)
        os.replace(tmp, _BLOCKS_FILE)
    except Exception:
        pass


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

    # Email notification
    send_trade_notification(record, exit_reasoning)

    # Lock 10% of any profit into the floor
    if gain_usd > 0:
        capital.lock_profit(gain_usd)
        logger.info(f"Profit lock: +${gain_usd * 0.10:.2f} added to floor (floor now ${capital.get_floor():.2f})")

    # Record stop-out cooldown so bot won't immediately re-enter this token
    if "stop_loss" in exit_rsn.lower() or "STOP LOSS" in exit_rsn:
        risk_mod.record_stopout(token)
        logger.info(f"Stop-out cooldown started for {token} ({risk_mod.COOLDOWN_MINUTES}min)")


def _validate_address(address: str, label: str = "") -> bool:
    """Validate an Ethereum address before using it in a transaction."""
    if not address or not isinstance(address, str):
        logger.warning(f"Invalid address (empty): {label}")
        return False
    if not address.startswith("0x") or len(address) != 42:
        logger.warning(f"Invalid address format: {address} ({label})")
        return False
    try:
        int(address, 16)
        return True
    except ValueError:
        logger.warning(f"Invalid address (non-hex): {address} ({label})")
        return False


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
            amount_wei,  # exact amount only — never grant unlimited approval
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

        fee: int = DEFAULT_FEE,
        entry_reasoning: str = "",
        exit_reasoning: str = "",
        cg_id: str = "",
    ) -> str | None:
        # Validate addresses before doing anything — prevents malformed addresses from reaching blockchain
        if not _validate_address(token_in_address, f"token_in:{token_in_symbol}"):
            return None
        if not _validate_address(token_out_address, f"token_out:{token_out_symbol}"):
            return None
        if amount_in_wei <= 0:
            logger.warning("Swap rejected: amount_in_wei must be positive")
            return None

        _amt_usd_approx = (amount_in_wei / 10 ** token_in_decimals) * (token_in_price_usd or 1)

        # Dusting attack protection: refuse to sell any token the bot never bought.
        # Malicious tokens are sometimes airdropped to wallets — interacting with them
        # (calling approve or transferring) can drain the wallet via malicious contracts.
        is_sell = token_in_symbol not in STABLECOINS and token_out_symbol in STABLECOINS
        if is_sell and token_in_symbol not in {"WETH", "cbBTC", "cbETH"}:
            open_pos = positions.get_open_positions()
            if token_in_symbol not in open_pos:
                msg = f"Dusting protection: {token_in_symbol} not in tracked positions — refusing sell"
                logger.warning(msg)
                _log_swap_block(token_in_symbol, token_out_symbol, _amt_usd_approx, msg)
                return None

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
            msg = f"No liquidity: {token_in_symbol}→{token_out_symbol} returned 0 quote on V3 and Aerodrome"
            logger.warning(msg)
            _log_swap_block(token_in_symbol, token_out_symbol, _amt_usd_approx, msg)
            return None

        # Price impact check — protect against near-zero liquidity pools
        amount_in_usd = (amount_in_wei / 10 ** token_in_decimals) * token_in_price_usd if token_in_price_usd > 0 else 0

        # Block buys when we have no reference price for the token we're buying
        is_buy = token_in_symbol in STABLECOINS and token_out_symbol not in STABLECOINS
        if is_buy and token_out_price_usd <= 0:
            msg = f"No reference price for {token_out_symbol} — cannot verify execution quality"
            logger.warning(msg)
            _log_swap_block(token_in_symbol, token_out_symbol, _amt_usd_approx, msg)
            return None

        if token_in_price_usd > 0 and token_out_price_usd > 0:
            # Use known decimals for registry tokens; fall back to 18 for custom tokens.
            # IMPORTANT: do NOT use a stablecoin check — DAI is a stablecoin with 18 decimals,
            # not 6. Always use TOKENS dict or default 18.
            token_out_dec = TOKENS.get(token_out_symbol, {}).get("decimals", 18)
            amount_out_tokens = amount_out / (10 ** token_out_dec)
            amount_out_usd    = amount_out_tokens * token_out_price_usd

            # Price impact check — buys only. Sells must be allowed through at any slippage;
            # being permanently stuck in a position is worse than accepting DEX illiquidity.
            # Dusting protection already handles sell-side security.
            if is_buy and amount_in_usd > 0:
                price_impact = (amount_in_usd - amount_out_usd) / amount_in_usd
                logger.info(f"Price impact: {price_impact:.1%} (in ${amount_in_usd:.2f} → out ${amount_out_usd:.2f})")
                if price_impact > MAX_PRICE_IMPACT:
                    msg = f"Price impact {price_impact:.1%} exceeds {MAX_PRICE_IMPACT:.0%} max for {token_in_symbol}→{token_out_symbol}"
                    logger.warning(msg)
                    _log_swap_block(token_in_symbol, token_out_symbol, amount_in_usd, msg)
                    return None
            elif not is_buy and amount_in_usd > 0:
                price_impact = (amount_in_usd - amount_out_usd) / amount_in_usd
                logger.info(f"Sell price impact: {price_impact:.1%} (in ${amount_in_usd:.2f} → out ${amount_out_usd:.2f}) — allowing exit")

            # Execution price check: compare on-chain implied price to CoinGecko reference.
            # Catches cases where DEX pool price is completely disconnected from market.
            if is_buy and amount_out_tokens > 0 and amount_in_usd > 0:
                implied_price = amount_in_usd / amount_out_tokens
                price_premium = (implied_price - token_out_price_usd) / token_out_price_usd
                logger.info(f"Execution price check: implied ${implied_price:.6f} vs reference ${token_out_price_usd:.6f} ({price_premium:+.1%})")
                if price_premium > 0.10:
                    msg = (f"On-chain price ${implied_price:.6f} is {price_premium:.1%} above "
                           f"CoinGecko ${token_out_price_usd:.6f} for {token_out_symbol} — pool likely empty")
                    logger.warning(msg)
                    _log_swap_block(token_in_symbol, token_out_symbol, amount_in_usd, msg)
                    return None

        # Gas cost check — only block buys if gas > 2% of trade size.
        # Never block sells on gas: being stuck in a position costs more than gas.
        if is_buy and token_in_price_usd > 0:
            current_gas_price = self.w3.eth.gas_price
            gas_cost_eth = (GAS_LIMIT * current_gas_price) / 1e18
            eth_price = token_in_price_usd if token_in_symbol == "WETH" else token_out_price_usd if token_out_symbol == "WETH" else price_eth_usd
            gas_cost_usd = gas_cost_eth * eth_price if eth_price > 0 else 0
            trade_usd = (amount_in_wei / 10 ** token_in_decimals) * token_in_price_usd
            if gas_cost_usd > 0 and trade_usd > 0 and (gas_cost_usd / trade_usd) > 0.02:
                msg = f"Gas ${gas_cost_usd:.3f} is {gas_cost_usd/trade_usd:.1%} of trade ${trade_usd:.2f} — exceeds 2%"
                logger.warning(msg)
                _log_swap_block(token_in_symbol, token_out_symbol, trade_usd, msg)
                return None

        # TheGraph liquidity check for buys — ensures pool has enough depth
        if is_buy and token_out_address:
            trade_usd_est = (amount_in_wei / 10 ** token_in_decimals) * (token_in_price_usd or 1)
            graph_check = check_pool_liquidity(token_out_address, trade_usd_est)
            if not graph_check["ok"]:
                logger.warning(f"TheGraph liquidity check failed: {graph_check['reason']}")
                # Don't hard-block — TheGraph can be wrong. Log as warning and continue.
                # The on-chain quote and price impact check will catch bad pools.

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
            # Simulate transaction before submission to avoid wasted gas on reverts
            try:
                self.w3.eth.call({
                    "from":  self.wallet.address,
                    "to":    tx["to"],
                    "data":  tx["data"],
                    "value": tx.get("value", 0),
                    "gas":   tx["gas"],
                })
            except Exception as sim_err:
                msg = f"Transaction simulation failed for {token_in_symbol}→{token_out_symbol}: {sim_err}"
                logger.warning(msg)
                _log_swap_block(token_in_symbol, token_out_symbol, _amt_usd_approx, msg)
                return None

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

            if status != "success":
                msg = f"On-chain swap failed (tx reverted): {token_in_symbol}→{token_out_symbol} | tx {tx_hash[:12]}"
                logger.warning(msg)
                _log_swap_block(token_in_symbol, token_out_symbol, _amt_usd_approx, msg)

            if status == "success":
                amount_in_tokens  = amount_in_wei / 10 ** token_in_decimals
                _out_dec          = TOKENS.get(token_out_symbol, {}).get("decimals", 18)
                amount_out_tokens = amount_out / (10 ** _out_dec)

                # Buying a non-stable token → open position
                if token_in_symbol in STABLECOINS and token_out_symbol not in STABLECOINS:
                    positions.open_position(
                        symbol=token_out_symbol,
                        amount_tokens=amount_out_tokens,
                        entry_price_usd=token_out_price_usd,
                        tx_hash=tx_hash,
                        take_profit_pct=take_profit_pct,
                        stop_loss_pct=stop_loss_pct,
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
