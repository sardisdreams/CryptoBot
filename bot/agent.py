import os
import httpx
import certifi
import anthropic
from bot.config import ANTHROPIC_API_KEY, TOKENS
from bot.portfolio import Portfolio
from bot.executor import Executor
from bot.logger import setup_logger
from bot import history, wiki
from bot.screener import get_screening_report
from bot.evaluator import score_coin, format_report

logger = setup_logger("agent")

NOTES_FILE = "notes.txt"

def _read_notes() -> list[str]:
    if not os.path.exists(NOTES_FILE):
        return []
    lines = []
    with open(NOTES_FILE) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                lines.append(line)
    return lines

SYSTEM_PROMPT = """You are an autonomous crypto trading agent operating on the Base blockchain.

Your goal is to grow total USD portfolio value aggressively but safely, targeting 10%+ returns.
BTC and ETH alone are not enough for this — the real opportunities are in Base-native low/mid cap tokens ($5M–$500M market cap).

## Strategy
- **Primary targets:** Base-native coins with real projects, growing TVL/activity, and upcoming catalysts
- **ETH/cbBTC:** Secondary — hold as safe harbor only when no better opportunities exist
- **USDC:** Keep at least 30% as reserve. Always cycle profits back to USDC after a trade closes.
- **Position sizing:** 5–15% per low-cap coin. Never over-concentrate.
- **Always keep 0.005 ETH minimum for gas.**

## Finding opportunities
Each tick you receive:
- Base ecosystem coins with market caps, price momentum, and volume
- Top 24h gainers across all crypto
- DeFiLlama Base protocols with TVL changes
- Technical indicators (RSI, SMA, momentum) for whitelisted coins
- Fear & Greed index and trending tokens

Look for:
- Rising TVL + rising price = genuine adoption signal
- RSI <30 on a solid project = oversold buying opportunity
- Upcoming catalysts mentioned in analyst notes
- Low-cap coins with strong volume relative to market cap (real interest, not pumped)

## Before trading a new coin
Use the evaluate_coin tool with the CoinGecko ID to get a safety score.
Only recommend adding a coin to the whitelist if it scores 50+ and has no critical flags.
You cannot trade coins not in the approved whitelist — but you CAN recommend new ones for review.

## Pump and dump protection
- Sudden >20% spike with no news or catalyst = likely pump, avoid
- Volume/market cap ratio >5x = suspicious
- Trending on CoinGecko with no fundamentals = warning sign
- Token under 3 months old = skip

## Token safety
- ONLY trade whitelisted tokens
- If something looks too good to be true, evaluate it first with evaluate_coin
- Capital preservation beats FOMO

Always reason step by step before acting."""

TOOLS = [
    {
        "name": "evaluate_coin",
        "description": "Run a safety and risk evaluation on a coin using its CoinGecko ID. Use this before recommending any new token for the watchlist or whitelist.",
        "input_schema": {
            "type": "object",
            "properties": {
                "cg_id": {
                    "type": "string",
                    "description": "The CoinGecko coin ID (e.g. 'ethereum', 'venice-token')",
                },
            },
            "required": ["cg_id"],
        },
    },
    {
        "name": "execute_swap",
        "description": "Execute a token swap on Uniswap V3 on Base chain.",
        "input_schema": {
            "type": "object",
            "properties": {
                "token_in": {
                    "type": "string",
                    "description": "Symbol of the token to sell (e.g. WETH, USDC, cbBTC)",
                },
                "token_out": {
                    "type": "string",
                    "description": "Symbol of the token to buy",
                },
                "amount_usd": {
                    "type": "number",
                    "description": "USD value of the trade",
                },
                "reasoning": {
                    "type": "string",
                    "description": "Why you are making this trade",
                },
            },
            "required": ["token_in", "token_out", "amount_usd", "reasoning"],
        },
    }
]


class TradingAgent:
    def __init__(self, portfolio: Portfolio, executor: Executor):
        self.portfolio = portfolio
        self.executor = executor
        self.client = anthropic.Anthropic(
            api_key=ANTHROPIC_API_KEY,
            http_client=httpx.Client(verify=certifi.where()),
        )

    def _build_market_prompt(self, snapshot: dict) -> str:
        lines = [
            f"Total portfolio value: ${snapshot['total_usd']:,.2f}",
            "",
            "Current holdings:",
        ]
        for symbol, h in snapshot["holdings"].items():
            if h["balance"] > 0:
                lines.append(
                    f"  {symbol}: {h['balance']:.6f} units | "
                    f"${h['value_usd']:,.2f} | {h['allocation_pct']:.1f}% of portfolio | "
                    f"Price: ${h['price_usd']:,.4f}"
                )

        lines += ["", "Current market prices (USD):"]
        for symbol, price in snapshot["prices"].items():
            lines.append(f"  {symbol}: ${price:,.4f}")

        fg = snapshot.get("fear_and_greed", {})
        lines += [
            "",
            f"Fear & Greed Index: {fg.get('value', 'N/A')} / 100 — {fg.get('label', 'Unknown')}",
            "(0=Extreme Fear, 50=Neutral, 100=Extreme Greed)",
        ]

        trending = snapshot.get("trending_tokens", [])
        if trending:
            lines += ["", "Currently trending on CoinGecko (monitor for pump-and-dump risk):"]
            for t in trending:
                rank = t.get("market_cap_rank") or "unranked"
                lines.append(f"  {t['symbol']} ({t['name']}) — market cap rank: {rank}")

        # Technical indicators from price history
        crypto_symbols = [s for s in TOKENS if s not in {"USDC", "USDT", "DAI"}]
        indicators = history.get_all_indicators(crypto_symbols)
        lines += ["", "Technical indicators (from stored price history):"]
        for sym, ind in indicators.items():
            n = ind["data_points"]
            if n < 2:
                lines.append(f"  {sym}: insufficient history ({n} data points — building up)")
                continue
            rsi = f"RSI={ind['rsi_14']}" if ind["rsi_14"] else "RSI=n/a"
            trend = ind["trend"] or "n/a"
            m1h = f"{ind['momentum_1h_pct']:+.2f}%" if ind["momentum_1h_pct"] is not None else "n/a"
            m4h = f"{ind['momentum_4h_pct']:+.2f}%" if ind["momentum_4h_pct"] is not None else "n/a"
            lines.append(f"  {sym}: {rsi} | trend={trend} | 1h={m1h} | 4h={m4h} ({n} pts)")

        # Market momentum from CoinGecko (1h/24h change)
        market_data = snapshot.get("market_data", {})
        if market_data:
            lines += ["", "Live price momentum (from CoinGecko):"]
            for sym, d in market_data.items():
                if sym in {"USDC", "USDT", "DAI"}:
                    continue
                lines.append(
                    f"  {sym}: 1h {d.get('change_1h', 0):+.2f}% | "
                    f"24h {d.get('change_24h', 0):+.2f}% | "
                    f"24h range ${d.get('low_24h', 0):,.0f}–${d.get('high_24h', 0):,.0f}"
                )

        # Aerodrome top pools
        pools = snapshot.get("aerodrome_pools", [])
        if pools:
            lines += ["", "Top Aerodrome pools by volume (Base DEX activity):"]
            for p in pools[:5]:
                lines.append(f"  {p['pair']}: ${p['volume_usd']:,.0f} volume")

        # Manual catalyst notes
        notes = _read_notes()
        if notes:
            lines += ["", "Analyst notes / upcoming catalysts (from notes.txt):"]
            for note in notes:
                lines.append(f"  - {note}")

        lines += [
            "",
            "Approved tokens for trading (whitelist only):",
            ", ".join(TOKENS.keys()),
        ]

        # Base ecosystem discovery
        base_coins = snapshot.get("base_ecosystem", [])
        if base_coins:
            lines += ["", "Base ecosystem coins ($5M–$500M market cap, sorted by volume):"]
            for c in base_coins[:10]:
                lines.append(
                    f"  {c['symbol']} ({c['name']}): ${c['price']:.4f} | "
                    f"mcap ${c['market_cap']/1e6:.1f}M | "
                    f"1h {c['change_1h']:+.1f}% | 24h {c['change_24h']:+.1f}% | "
                    f"vol ${c['volume_24h']/1e3:.0f}K | cgid: {c['cg_id']}"
                )

        gainers = snapshot.get("top_gainers", [])
        if gainers:
            lines += ["", "Top 24h gainers (cross-market — investigate before acting):"]
            for g in gainers[:5]:
                lines.append(f"  {g['symbol']} ({g['name']}): {g['change']:+.1f}% | vol ${g['volume']/1e3:.0f}K")

        defillama = snapshot.get("defillama_base", [])
        if defillama:
            lines += ["", "Base DeFi protocols by TVL change (rising TVL = growing adoption):"]
            for p in defillama[:8]:
                sym = f" ({p['symbol']})" if p.get("symbol") else ""
                lines.append(
                    f"  {p['name']}{sym}: TVL ${p['tvl']/1e6:.1f}M | "
                    f"1d change {p.get('change_1d', 0):+.1f}%"
                )

        # Coin wiki summaries
        wiki_text = wiki.get_all_summaries(list(TOKENS.keys()))
        if wiki_text:
            lines += ["", "=== COIN WIKI (research & trading notes) ===", wiki_text]

        # Watchlist tokens (not tradeable yet but worth noting)
        watchlist = wiki.get_watchlist()
        if watchlist:
            lines += ["", "Watchlist tokens (NOT tradeable — under review):"]
            for w in watchlist:
                lines.append(f"  {w.get('symbol')} ({w.get('name')}) — Risk: {w.get('risk')} — {w.get('contract', 'no contract yet')}")

        return "\n".join(lines)

    def _handle_tool(self, tool_name: str, tool_input: dict, snapshot: dict) -> str:
        if tool_name == "evaluate_coin":
            cg_id = tool_input.get("cg_id", "")
            logger.info(f"Evaluating coin: {cg_id}")
            evaluation = score_coin(cg_id)
            return format_report(evaluation)
        return self._execute_tool(tool_input, snapshot)

    def _execute_tool(self, tool_input: dict, snapshot: dict) -> str:
        token_in_sym = tool_input["token_in"].upper()
        token_out_sym = tool_input["token_out"].upper()
        amount_usd = float(tool_input["amount_usd"])
        reasoning = tool_input.get("reasoning", "")

        logger.info(f"Agent decision: {token_in_sym} → {token_out_sym} | ${amount_usd:.2f} | {reasoning}")

        token_in = TOKENS.get(token_in_sym)
        token_out = TOKENS.get(token_out_sym)

        # ETH (native) maps to WETH for routing
        if token_in_sym == "ETH":
            token_in = TOKENS["WETH"]
            token_in_sym = "WETH"
        if token_out_sym == "ETH":
            token_out = TOKENS["WETH"]
            token_out_sym = "WETH"

        if not token_in or not token_out:
            return f"Error: unknown token symbol. Available: {', '.join(TOKENS.keys())}"

        price_in = snapshot["prices"].get(token_in_sym, 0)
        if price_in == 0:
            return f"Error: could not get price for {token_in_sym}"

        # Convert USD amount to token units
        amount_tokens = amount_usd / price_in
        amount_wei = int(amount_tokens * (10 ** token_in["decimals"]))

        # Check available balance
        holding = snapshot["holdings"].get(token_in_sym, {})
        available_usd = holding.get("value_usd", 0)
        if amount_usd > available_usd * 0.98:
            return f"Insufficient balance: have ${available_usd:.2f} of {token_in_sym}, need ${amount_usd:.2f}"

        tx_hash = self.executor.swap(
            token_in_address=token_in["address"],
            token_in_symbol=token_in_sym,
            token_in_decimals=token_in["decimals"],
            token_out_address=token_out["address"],
            token_out_symbol=token_out_sym,
            amount_in_wei=amount_wei,
            price_eth_usd=snapshot["prices"].get("WETH", 0),
        )

        if tx_hash:
            return f"Swap submitted: {tx_hash}"
        return "Swap failed — check logs"

    def run_once(self):
        logger.info("Agent tick: fetching portfolio snapshot...")
        snapshot = self.portfolio.get_snapshot()
        context = self.portfolio.market.get_full_context()
        snapshot["fear_and_greed"] = context["fear_and_greed"]
        snapshot["trending_tokens"] = context["trending_tokens"]
        snapshot["market_data"] = context["market_data"]
        snapshot["aerodrome_pools"] = context.get("aerodrome_pools", [])

        # Record prices to build up history for technical indicators
        history.record_prices(context["prices"])

        # Discover new opportunities
        screening = get_screening_report()
        snapshot["base_ecosystem"] = screening.get("base_ecosystem", [])
        snapshot["top_gainers"] = screening.get("top_gainers_24h", [])
        snapshot["defillama_base"] = screening.get("defillama_base", [])

        market_context = self._build_market_prompt(snapshot)

        messages = [{"role": "user", "content": market_context}]

        response = self.client.messages.create(
            model="claude-opus-4-8",
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        # Agentic loop — handle tool calls until model stops
        while response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = self._handle_tool(block.name, block.input, snapshot)
                    logger.info(f"Tool result: {result}")
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })

            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})

            response = self.client.messages.create(
                model="claude-opus-4-8",
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=messages,
            )

        # Log final reasoning
        for block in response.content:
            if hasattr(block, "text"):
                logger.info(f"Agent reasoning: {block.text}")
