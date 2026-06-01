import os
import httpx
import certifi
import anthropic
from bot.config import ANTHROPIC_API_KEY, TOKENS, MAX_DEPLOY_USD, MIN_TRADE_USD, MAX_TRADE_USD
from bot.portfolio import Portfolio
from bot.executor import Executor
from bot.logger import setup_logger
from bot import history, wiki, positions, blacklist
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

SYSTEM_PROMPT = f"""You are an autonomous crypto trading agent operating on the Base blockchain.

Your goal is to find and execute short-term trading opportunities — hours to a few days, not weeks.
This is NOT a buy-and-hold portfolio. Get in, capture the move, get back to USDC.

## Capital rules (strict)
- Total deployed capital across ALL open positions: max ${MAX_DEPLOY_USD:.0f}
- Single trade size: ${MIN_TRADE_USD:.0f}–${MAX_TRADE_USD:.0f}
- The remaining ~$300 USDC stays as reserve — do not touch it
- Always keep at least 0.005 ETH for gas
- These limits exist because we are in testing phase — respect them exactly

## Trading style
- Short-term swing trades: target hold time is hours to 2-3 days maximum
- Exit when the momentum that got you in starts fading — don't wait for the "full move"
- Frequent small wins beat rare big wins
- If a trade isn't working within a few hours, exit and redeploy elsewhere
- Do NOT hold hoping for recovery — cut losses quickly (25% down = exit)

## Primary targets
- Base-native coins with real projects, $5M–$500M market cap
- ETH/cbBTC: only if a clear short-term technical setup appears
- Always sell back to USDC after closing — never rotate directly between crypto assets

## Finding opportunities
Each tick you receive prices, RSI, momentum, volume, TVL data, trending tokens, and analyst notes.
One strong signal is enough to act. RSI informs your view but is not a hard requirement.

### The 5 entry setups to look for:

**Setup 1 — Momentum Continuation**
Token showing >2% positive 1h momentum with above-average volume and RSI below 70.
The move has conviction and room to run. Buy 5–15%.

**Setup 2 — Dip Recovery**
Token down 5–15% over 24h but 1h momentum turning positive. RSI below 50.
Buyers stepping in after a sell-off. Buy 10% on the turn.
Warning: if 1h stalls again, exit — don't catch a falling knife.

**Setup 3 — Catalyst Trade**
Upcoming event in analyst notes (emission change, launch, listing, partnership).
Enter before the event, sell into the reaction regardless of how it feels.
Buy 10–15% if liquidity is sufficient.

**Setup 4 — TVL Rising**
DeFiLlama shows a Base protocol TVL up >5% in 24h with positive price momentum.
Real capital inflow = genuine adoption. Buy 5–10%.
Check it's not a single whale deposit.

**Setup 5 — RSI Oversold Recovery**
RSI below 30 AND 1h momentum just turned positive. Oversold condition resolving.
High conviction — buy up to 15%. Only use on tokens with real liquidity.

### Exits
No fixed rules — read the conditions. Sell into strength when momentum fades.
If the trade thesis breaks, exit regardless of P&L. Always sell back to USDC.

## Trading any coin
You can trade ANY token on Base — no whitelist. Use get_token_info to look up a coin's
contract address and decimals before trading it. Use evaluate_coin to check safety if uncertain.

## Pump and dump protection
- Sudden >20% spike with no news = likely pump, use caution
- Volume/market cap >5x = suspicious, evaluate first
- Token under 3 months old = high risk, size small
- Use your judgment — don't ask for permission, act on conviction

Always reason step by step before acting."""

TOOLS = [
    {
        "name": "get_token_info",
        "description": "Look up a token's Base chain contract address, decimals, and current price by CoinGecko ID. Use this before trading any token not already in your holdings.",
        "input_schema": {
            "type": "object",
            "properties": {
                "cg_id": {"type": "string", "description": "CoinGecko coin ID (e.g. 'venice-token', 'aerodrome-finance')"},
            },
            "required": ["cg_id"],
        },
    },
    {
        "name": "evaluate_coin",
        "description": "Run a safety and risk evaluation on a coin. Returns a score 0-100 and flags. Use when uncertain about a token.",
        "input_schema": {
            "type": "object",
            "properties": {
                "cg_id": {"type": "string", "description": "CoinGecko coin ID"},
            },
            "required": ["cg_id"],
        },
    },
    {
        "name": "execute_swap",
        "description": "Execute a token swap on Base chain. For known tokens (USDC, WETH, cbBTC, cbETH) just provide the symbol. For any other token, provide contract_address and decimals from get_token_info. When buying, always set take_profit_pct and stop_loss_pct.",
        "input_schema": {
            "type": "object",
            "properties": {
                "token_in":           {"type": "string",  "description": "Symbol of token to sell"},
                "token_in_address":   {"type": "string",  "description": "Contract address — only needed if token_in is unlisted"},
                "token_in_decimals":  {"type": "integer", "description": "Decimals — only needed if token_in is unlisted"},
                "token_out":          {"type": "string",  "description": "Symbol of token to buy"},
                "token_out_address":  {"type": "string",  "description": "Contract address — only needed if token_out is unlisted"},
                "token_out_decimals": {"type": "integer", "description": "Decimals — only needed if token_out is unlisted"},
                "amount_usd":         {"type": "number",  "description": "USD value of the trade"},
                "take_profit_pct":    {"type": "number",  "description": "Take profit % above entry (e.g. 20 = sell 50% when up 20%). Required when buying."},
                "stop_loss_pct":      {"type": "number",  "description": "Stop loss % below entry (e.g. 20 = sell all when down 20%). Required when buying."},
                "max_hold_hours":     {"type": "number",  "description": "Hours before starting to look for exit (default 48). Soft suggestion, not forced."},
                "reasoning":          {"type": "string",  "description": "Why you are making this trade"},
            },
            "required": ["token_in", "token_out", "amount_usd", "reasoning"],
        },
    },
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
        # Open positions with P&L
        open_pos = positions.get_position_summary(snapshot.get("prices", {}))
        realized = positions.get_realized_summary()

        currently_deployed = sum(p["current_value"] for p in open_pos)
        deploy_remaining = max(0, MAX_DEPLOY_USD - currently_deployed)

        lines = [
            f"Total portfolio value: ${snapshot['total_usd']:,.2f}",
            f"Deployment cap: ${currently_deployed:.2f} deployed of ${MAX_DEPLOY_USD:.0f} max "
            f"(${deploy_remaining:.2f} available to deploy)",
            f"Trade size limits: ${MIN_TRADE_USD:.0f}–${MAX_TRADE_USD:.0f} per trade",
            f"Total realized gains: ${realized['total_realized_gain_usd']:+,.2f} "
            f"(short-term: ${realized['short_term_gain_usd']:+,.2f} | "
            f"long-term: ${realized['long_term_gain_usd']:+,.2f})",
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

        # Open positions detail
        time_suggestions = snapshot.get("_time_exit_suggestions", [])
        if time_suggestions:
            lines += ["", "Hold window suggestions (soft — use your judgment):"]
            for s in time_suggestions:
                lines.append(f"  - {s}")

        if open_pos:
            lines += ["", "Open positions (entry price → current P&L):"]
            for p in open_pos:
                lines.append(
                    f"  {p['symbol']}: {p['amount_tokens']:.6f} tokens | "
                    f"entry ${p['entry_price']:,.4f} | now ${p['current_price']:,.4f} | "
                    f"P&L ${p['gain_loss_usd']:+,.2f} ({p['gain_loss_pct']:+.2f}%) | "
                    f"held {p['hold_days']}d"
                )
                if p['gain_loss_pct'] <= -25:
                    lines.append(f"    ⚠️  STOP-LOSS TRIGGERED — down {p['gain_loss_pct']:.1f}%, consider exiting")
                elif p['gain_loss_pct'] >= 60:
                    lines.append(f"    🎯 TAKE PROFIT L3 — up {p['gain_loss_pct']:.1f}%, consider selling 25%")
                elif p['gain_loss_pct'] >= 40:
                    lines.append(f"    🎯 TAKE PROFIT L2 — up {p['gain_loss_pct']:.1f}%, consider selling 25%")
                elif p['gain_loss_pct'] >= 20:
                    lines.append(f"    🎯 TAKE PROFIT L1 — up {p['gain_loss_pct']:.1f}%, consider selling 25%")

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

        # Top 5 Base ecosystem movers only (sorted by abs 1h change)
        base_coins = snapshot.get("base_ecosystem", [])
        if base_coins:
            top5 = sorted(base_coins, key=lambda c: abs(c.get("change_1h", 0)), reverse=True)[:5]
            lines += ["", "Top Base movers (1h):"]
            for c in top5:
                lines.append(
                    f"  {c['symbol']}: ${c['price']:.4f} | "
                    f"1h {c['change_1h']:+.1f}% | 24h {c['change_24h']:+.1f}% | "
                    f"mcap ${c['market_cap']/1e6:.0f}M | cgid:{c['cg_id']}"
                )

        gainers = snapshot.get("top_gainers", [])
        if gainers:
            lines += ["", "Top gainers (24h):"]
            for g in gainers[:3]:
                lines.append(f"  {g['symbol']}: {g['change']:+.1f}% | cgid:{g['cg_id']}")

        # DeFiLlama — top 5 Base protocols with biggest TVL change only
        defillama = snapshot.get("defillama_base", [])
        if defillama:
            top_tvl = [p for p in defillama if p.get("symbol") and abs(p.get("change_1d") or 0) > 2][:5]
            if top_tvl:
                lines += ["", "Base protocols with significant TVL change:"]
                for p in top_tvl:
                    lines.append(f"  {p['name']} ({p['symbol']}): TVL ${p['tvl']/1e6:.0f}M | {p.get('change_1d', 0):+.1f}%")

        # Wiki only for tokens currently held (not entire registry)
        held_symbols = [sym for sym, h in snapshot.get("holdings", {}).items() if h.get("balance", 0) > 0.000001 and sym not in {"USDC","USDT","DAI","ETH"}]
        if held_symbols:
            wiki_text = wiki.get_all_summaries(held_symbols)
            if wiki_text:
                lines += ["", "Wiki for held tokens:", wiki_text]

        return "\n".join(lines)

    def _get_token_info(self, cg_id: str) -> str:
        import requests as req
        try:
            resp = req.get(
                f"https://api.coingecko.com/api/v3/coins/{cg_id}",
                params={"localization": "false", "tickers": "false"},
                timeout=15,
                verify=certifi.where(),
            )
            resp.raise_for_status()
            data = resp.json()
            platforms = data.get("detail_platforms", {}) or data.get("platforms", {})
            base_info = platforms.get("base", {})
            contract = base_info.get("contract_address") if isinstance(base_info, dict) else base_info
            decimals = base_info.get("decimal_place", 18) if isinstance(base_info, dict) else 18
            price = data.get("market_data", {}).get("current_price", {}).get("usd", 0)
            name = data.get("name", cg_id)
            if not contract:
                return f"{name}: not found on Base chain. May not be deployed on Base."
            return (
                f"{name} ({cg_id})\n"
                f"Base contract: {contract}\n"
                f"Decimals: {decimals}\n"
                f"Price: ${price:,.6f}\n"
                f"Use these values in execute_swap."
            )
        except Exception as e:
            return f"Could not fetch token info for {cg_id}: {e}"

    def _handle_tool(self, tool_name: str, tool_input: dict, snapshot: dict) -> str:
        if tool_name == "get_token_info":
            cg_id = tool_input.get("cg_id", "")
            logger.info(f"Fetching token info: {cg_id}")
            return self._get_token_info(cg_id)
        if tool_name == "evaluate_coin":
            cg_id = tool_input.get("cg_id", "")
            logger.info(f"Evaluating coin: {cg_id}")
            evaluation = score_coin(cg_id)
            return format_report(evaluation)
        return self._execute_tool(tool_input, snapshot)

    def _execute_tool(self, tool_input: dict, snapshot: dict) -> str:
        token_in_sym  = tool_input["token_in"].upper()
        token_out_sym = tool_input["token_out"].upper()
        amount_usd    = float(tool_input["amount_usd"])
        reasoning     = tool_input.get("reasoning", "")

        logger.info(f"Agent decision: {token_in_sym} → {token_out_sym} | ${amount_usd:.2f} | {reasoning}")

        # ETH alias
        if token_in_sym == "ETH":  token_in_sym = "WETH"
        if token_out_sym == "ETH": token_out_sym = "WETH"

        # Resolve token_in — known registry or custom address
        if token_in_sym in TOKENS:
            token_in = TOKENS[token_in_sym]
        elif tool_input.get("token_in_address"):
            token_in = {
                "address":  tool_input["token_in_address"],
                "decimals": int(tool_input.get("token_in_decimals", 18)),
                "symbol":   token_in_sym,
            }
        else:
            return f"Unknown token '{token_in_sym}' — use get_token_info to look up its contract address first"

        # Resolve token_out
        if token_out_sym in TOKENS:
            token_out = TOKENS[token_out_sym]
        elif tool_input.get("token_out_address"):
            token_out = {
                "address":  tool_input["token_out_address"],
                "decimals": int(tool_input.get("token_out_decimals", 18)),
                "symbol":   token_out_sym,
            }
        else:
            return f"Unknown token '{token_out_sym}' — use get_token_info to look up its contract address first"

        # Get price for token_in
        price_in = snapshot["prices"].get(token_in_sym, 0)
        if price_in == 0 and token_in_sym not in {"USDC","USDT","DAI"}:
            return f"Could not get price for {token_in_sym} — run get_token_info first"
        if token_in_sym in {"USDC","USDT","DAI"}:
            price_in = 1.0

        # Convert USD amount to token units
        amount_tokens = amount_usd / price_in
        amount_wei = int(amount_tokens * (10 ** token_in["decimals"]))

        # Check blacklist
        if blacklist.is_blocked(token_out_sym):
            return f"{token_out_sym} is on your block list — trade cancelled"

        # Enforce minimum trade size
        if amount_usd < MIN_TRADE_USD:
            return f"Trade too small: ${amount_usd:.2f} is below minimum ${MIN_TRADE_USD:.2f} (gas not worth it)"

        # Enforce maximum single trade size
        if amount_usd > MAX_TRADE_USD:
            amount_usd = MAX_TRADE_USD
            logger.info(f"Trade capped at ${MAX_TRADE_USD:.0f} (testing phase limit)")

        # Enforce total deployment cap
        open_pos = positions.get_position_summary(snapshot.get("prices", {}))
        currently_deployed = sum(p["current_value"] for p in open_pos)
        if token_in_sym in {"USDC", "USDT", "DAI"}:  # buying crypto
            if currently_deployed + amount_usd > MAX_DEPLOY_USD:
                remaining = MAX_DEPLOY_USD - currently_deployed
                if remaining < MIN_TRADE_USD:
                    return f"Deployment cap reached: ${currently_deployed:.2f} already deployed of ${MAX_DEPLOY_USD:.0f} max"
                amount_usd = min(amount_usd, remaining)
                logger.info(f"Trade reduced to ${amount_usd:.2f} to stay within ${MAX_DEPLOY_USD:.0f} cap")

        # Recalculate amount_wei after any cap adjustments
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
            token_in_price_usd=price_in,
            token_out_price_usd=snapshot["prices"].get(token_out_sym, 0),
            take_profit_pct=float(tool_input.get("take_profit_pct", 25.0)),
            stop_loss_pct=float(tool_input.get("stop_loss_pct", 25.0)),
            max_hold_hours=float(tool_input.get("max_hold_hours", 48.0)),
        )

        if tx_hash:
            return f"Swap submitted: {tx_hash}"
        return "Swap failed — check logs"

    def _is_market_active(self, snapshot: dict) -> bool:
        """
        Quick check: are there any interesting signals this tick?
        If not, use Haiku (cheap). If yes, escalate to Sonnet (better reasoning).
        """
        fg = snapshot.get("fear_and_greed", {}).get("value", 50)
        market_data = snapshot.get("market_data", {})

        # Escalate if Fear & Greed is extreme
        if fg <= 20 or fg >= 80:
            logger.info("Market active: extreme Fear/Greed")
            return True

        # Escalate if any whitelisted coin moved >3% in 1h
        for sym, d in market_data.items():
            if abs(d.get("change_1h", 0)) >= 3:
                logger.info(f"Market active: {sym} moved {d['change_1h']:+.1f}% in 1h")
                return True

        # Escalate if screener found meaningful gainers (>5% in 24h)
        for coin in snapshot.get("top_gainers", []):
            if abs(coin.get("change", 0)) >= 5:
                logger.info(f"Market active: screener found {coin['symbol']} up {coin['change']:+.1f}%")
                return True

        # Escalate if analyst notes are present
        if _read_notes():
            logger.info("Market active: analyst notes present")
            return True

        # Escalate if RSI is oversold or overbought on any coin
        indicators = history.get_all_indicators([s for s in TOKENS if s not in {"USDC","USDT","DAI"}])
        for sym, ind in indicators.items():
            rsi = ind.get("rsi_14")
            if rsi and (rsi <= 30 or rsi >= 70):
                logger.info(f"Market active: {sym} RSI={rsi}")
                return True

        # Escalate if open positions need monitoring (P&L > ±15%)
        open_pos = positions.get_position_summary(snapshot.get("prices", {}))
        for p in open_pos:
            if abs(p.get("gain_loss_pct", 0)) >= 15:
                logger.info(f"Market active: {p['symbol']} P&L={p['gain_loss_pct']:+.1f}%")
                return True

        return False

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

        # ── Mechanical exits (TP/SL/time) — run before AI, no reasoning needed ──
        exits = positions.check_mechanical_exits(context["prices"])
        for ex in exits:
            sym     = ex["symbol"]
            amt     = ex["amount_tokens"]
            reason  = ex["reason"]
            urgency = ex["urgency"]
            logger.info(f"Mechanical exit triggered: {sym} | {reason}")

            # Stop loss and take profit execute immediately
            if ex["exit_type"] in ("stop_loss", "take_profit"):
                token_info = TOKENS.get(sym)
                usdc_info  = TOKENS["USDC"]
                if token_info:
                    amount_wei = int(amt * (10 ** token_info["decimals"]))
                    price = context["prices"].get(sym, 0)
                    self.executor.swap(
                        token_in_address=token_info["address"],
                        token_in_symbol=sym,
                        token_in_decimals=token_info["decimals"],
                        token_out_address=usdc_info["address"],
                        token_out_symbol="USDC",
                        amount_in_wei=amount_wei,
                        token_in_price_usd=price,
                        token_out_price_usd=1.0,
                    )
            # Time suggestion — just log it, agent will handle in its reasoning
            elif ex["exit_type"] == "time_suggestion":
                snapshot["_time_exit_suggestions"] = snapshot.get("_time_exit_suggestions", [])
                snapshot["_time_exit_suggestions"].append(reason)

        # Discover new opportunities
        screening = get_screening_report()

        # Filter out blacklisted tokens
        def not_blocked(coin: dict) -> bool:
            return not blacklist.is_blocked(
                coin.get("symbol", ""), coin.get("cg_id", "")
            )

        snapshot["base_ecosystem"] = [c for c in screening.get("base_ecosystem", []) if not_blocked(c)]
        snapshot["top_gainers"]    = [c for c in screening.get("top_gainers_24h", []) if not_blocked(c)]
        snapshot["defillama_base"] = screening.get("defillama_base", [])

        # Cache screener results for the dashboard
        import json
        os.makedirs("data", exist_ok=True)
        with open("data/screener_cache.json", "w") as _f:
            json.dump({
                "base_ecosystem": screening.get("base_ecosystem", []),
                "top_gainers":    screening.get("top_gainers_24h", []),
                "updated":        __import__("datetime").datetime.utcnow().isoformat(),
            }, _f)

        market_context = self._build_market_prompt(snapshot)

        # Two-tier model: Haiku for quiet markets, Sonnet when signals are active
        active = self._is_market_active(snapshot)
        model = "claude-sonnet-4-6" if active else "claude-haiku-4-5-20251001"
        logger.info(f"Using model: {model} (market active: {active})")

        messages = [{"role": "user", "content": market_context}]

        response = self.client.messages.create(
            model=model,
            max_tokens=2048 if not active else 4096,
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

            # Always use Sonnet for tool follow-ups (needs full reasoning)
            response = self.client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=messages,
            )

        # Log token usage and estimated cost
        usage = response.usage
        input_tokens  = usage.input_tokens
        output_tokens = usage.output_tokens
        # Sonnet: $3/M input, $15/M output | Haiku: $0.25/M input, $1.25/M output
        rates = {"claude-sonnet-4-6": (3.0, 15.0), "claude-haiku-4-5-20251001": (0.25, 1.25)}
        ir, or_ = rates.get(model, (3.0, 15.0))
        cost = (input_tokens * ir + output_tokens * or_) / 1_000_000
        logger.info(f"Tokens: {input_tokens} in / {output_tokens} out | Est. cost: ${cost:.4f}")

        # Log final reasoning
        for block in response.content:
            if hasattr(block, "text"):
                logger.info(f"Agent reasoning: {block.text}")
