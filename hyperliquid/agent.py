"""
Hyperliquid trading agent — decision layer.

Signal engine runs every tick (no API cost).
Claude (Haiku) is called only for borderline signals (50-64) or positions in crisis.
Auto-execution fires for strong signals (≥ 65) after risk guards pass.

The agent manages both entries and exits:
- Entries: auto-execute strong signals, Claude for borderline
- Exits: TP/SL enforced by Hyperliquid's own order system
         Time stops checked each tick (>48h with no TP/SL)
         Claude can call discretionary exit if macro deteriorates
"""
import os
import time
from datetime import datetime, timezone

import anthropic

from bot.logger import setup_logger
from bot import cost_tracker
from hyperliquid import market, signals, positions, risk, executor
from hyperliquid.config import (
    HL_PRIVATE_KEY, LEVERAGE, TRADE_SIZE_PCT, MIN_TRADE_USD, MAX_TRADE_USD,
    MAX_OPEN, AUTO_EXEC_MIN, CLAUDE_REVIEW_MIN, MAX_HOLD_HOURS, ANTHROPIC_BUDGET_USD,
    HL_BOT_VERSION,
)

logger = setup_logger("hl.agent")


SYSTEM_PROMPT = """You are an autonomous crypto perpetuals trading agent on Hyperliquid.
You trade BTC, ETH, and SOL perpetual contracts 24/7 with 2x leverage.

Your mandate:
- Go LONG in uptrends, SHORT in downtrends — profit from both directions
- Protect capital: never risk more than 3% per trade on notional
- Average multiple profitable trades per day through disciplined signal following
- Always assess whether macro (BTC direction) supports or contradicts the trade

Risk rules (not negotiable):
- Daily drawdown limit: 5% of account — if hit, no new trades today
- Win rate guard: < 35% over last 8 trades → hold cash
- Max positions: 2 simultaneously
- Leverage: 2x fixed — TP/SL orders enforced by the exchange itself

When you see a trade to take: call execute_trade immediately. Be decisive.
When you see a position to close early: call close_position. Protect profits.
When conditions are unclear: call no_action with your reasoning.
"""

SYSTEM_PROMPT_CACHED = [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral", "ttl": "1h"}}]

TOOLS = [
    {
        "name": "execute_trade",
        "description": "Open a leveraged perpetual position on Hyperliquid.",
        "input_schema": {
            "type": "object",
            "properties": {
                "coin":      {"type": "string",  "description": "BTC, ETH, or SOL"},
                "direction": {"type": "string",  "enum": ["long", "short"]},
                "size_usd":  {"type": "number",  "description": "USD margin to deploy (before leverage). Bot will apply 2x."},
                "reasoning": {"type": "string",  "description": "Why this trade, what is the thesis"},
                "confidence":{"type": "integer", "description": "Conviction 1-10. Trades with confidence < 6 are blocked."},
            },
            "required": ["coin", "direction", "size_usd", "reasoning", "confidence"],
        },
    },
    {
        "name": "close_position",
        "description": "Close an open position immediately (discretionary exit — not waiting for TP/SL).",
        "input_schema": {
            "type": "object",
            "properties": {
                "coin":    {"type": "string", "description": "Coin to close (BTC, ETH, SOL)"},
                "reason":  {"type": "string", "description": "Why closing early"},
            },
            "required": ["coin", "reason"],
        },
        "cache_control": {"type": "ephemeral", "ttl": "1h"},
    },
]


class HLAgent:

    def __init__(self):
        self.client  = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
        self.address = __import__("eth_account", fromlist=["Account"]).Account.from_key(HL_PRIVATE_KEY).address
        self._last_claude_ts = 0.0
        self._last_routine_ts = 0.0
        ROUTINE_COOLDOWN = 45 * 60

    def run_once(self):
        logger.info(f"HL tick | {HL_BOT_VERSION} | {datetime.now(timezone.utc).strftime('%H:%M UTC')}")

        # ── Market snapshot ───────────────────────────────────────────────────
        prices   = market.get_all_prices()
        funding  = market.get_funding_rates()
        margin   = market.get_margin_summary(self.address)
        account_usd = margin.get("account_value", 0)

        if account_usd <= 0:
            logger.warning("HL: account value is $0 — wallet not funded or API error")
            return

        # ── Open position sync ────────────────────────────────────────────────
        on_chain = market.get_open_perp_positions(self.address)
        on_chain_coins = {p["coin"] for p in on_chain}
        local_open = positions.get_open_positions()

        # Time-stop: close positions held past MAX_HOLD_HOURS with no exit
        for coin, pos in local_open.items():
            opened = datetime.fromisoformat(pos["opened_at"])
            hold_h = (datetime.now(timezone.utc) - opened).total_seconds() / 3600
            if hold_h > MAX_HOLD_HOURS and coin in on_chain_coins:
                logger.info(f"HL time-stop: {coin} held {hold_h:.0f}h > {MAX_HOLD_HOURS}h — closing")
                executor.close_position(coin, reason="time_stop")

        # ── Record portfolio value for drawdown tracking ──────────────────────
        risk.record_portfolio_value(account_usd)
        risk_state = risk.get_risk_summary(account_usd, len(on_chain), MAX_OPEN)
        logger.info(f"HL account: ${account_usd:.2f} | {len(on_chain)}/{MAX_OPEN} positions | risk_ok={risk_state['all_clear']}")

        # ── Score all coins ───────────────────────────────────────────────────
        scored = signals.score_all()

        # ── Auto-execute strong signals ───────────────────────────────────────
        auto_executed = None
        if risk_state["all_clear"]:
            auto_executed = self._auto_execute(scored, account_usd, prices, funding)

        # ── Claude review for borderline signals / open positions ─────────────
        if self._needs_claude_review(scored, on_chain, auto_executed):
            self._claude_review(scored, on_chain, prices, funding, risk_state, account_usd, auto_executed)

    def _auto_execute(self, scored: list[dict], account_usd: float, prices: dict, funding: dict) -> dict | None:
        available_usd = account_usd  # total available margin
        trade_usd     = min(max(available_usd * TRADE_SIZE_PCT, MIN_TRADE_USD), MAX_TRADE_USD)

        if trade_usd < MIN_TRADE_USD:
            logger.info(f"HL auto-execute: insufficient margin (${available_usd:.2f})")
            return None

        open_coins = set(positions.get_open_positions().keys())

        for candidate in scored:
            if not candidate.get("entry_ok"):
                continue
            if candidate["score"] < AUTO_EXEC_MIN:
                continue
            coin      = candidate["coin"]
            direction = candidate["direction"]
            price     = candidate["price"]

            if coin in open_coins:
                continue  # already positioned in this coin

            ok, reason = risk.can_open_trade(account_usd, len(open_coins), MAX_OPEN, coin)
            if not ok:
                logger.info(f"HL auto-execute: risk blocked {coin} — {reason}")
                continue

            # Funding rate check: avoid entering if funding strongly against us
            fr = funding.get(coin, 0)
            if direction == "long"  and fr >  0.001:  # paying >0.1% per 8h to be long
                logger.info(f"HL auto-execute: {coin} long skipped — high funding rate {fr:.4f}")
                continue
            if direction == "short" and fr < -0.001:  # paying to be short
                logger.info(f"HL auto-execute: {coin} short skipped — negative funding {fr:.4f}")
                continue

            logger.info(f"HL auto-execute: {direction.upper()} {coin} score={candidate['score']} @ ${price:.2f}")
            pos = executor.open_position(
                coin=coin,
                direction=direction,
                size_usd=trade_usd,
                price=price,
                reasoning=f"Auto-entry score={candidate['score']}/100 | RSI={candidate.get('rsi')} | {direction.upper()}",
            )
            if pos:
                open_coins.add(coin)
                candidate["auto_executed"] = True
                return candidate
            break  # one attempt per tick

        return None

    def _needs_claude_review(self, scored: list[dict], on_chain: list[dict], auto_executed) -> bool:
        if auto_executed:
            return False

        # Position in significant loss (>10% on margin = >5% on notional at 2x)
        for pos in on_chain:
            pnl_pct = pos.get("unrealized_pnl", 0) / 1 * 100  # rough
            if abs(pos.get("unrealized_pnl", 0)) > 15:
                return True

        # Borderline signal worth a second opinion
        borderline = [c for c in scored if CLAUDE_REVIEW_MIN <= c["score"] < AUTO_EXEC_MIN and c.get("entry_ok")]
        if borderline:
            elapsed = time.time() - self._last_routine_ts
            if elapsed > 45 * 60:  # 45-min cooldown on routine reviews
                return True

        return False

    def _claude_review(self, scored, on_chain, prices, funding, risk_state, account_usd, auto_executed):
        budget = float(os.getenv("ANTHROPIC_BUDGET_USD", str(ANTHROPIC_BUDGET_USD)))
        spent  = cost_tracker.get_summary().get("anthropic_today", 0)
        if spent > budget * 0.9:
            logger.warning(f"HL Claude: budget {spent:.2f}/{budget:.2f} — skipping review")
            return

        pos_summary = positions.get_position_summary(prices)
        open_coins  = {p["coin"] for p in pos_summary}

        context_lines = [
            f"=== Hyperliquid Bot Review — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} ===",
            f"Account: ${account_usd:.2f} | Positions: {len(on_chain)}/{MAX_OPEN} | Leverage: {LEVERAGE}x",
            "",
            "=== OPEN POSITIONS ===",
        ]
        if pos_summary:
            for p in pos_summary:
                context_lines.append(
                    f"  {p['coin']} {p['direction'].upper()} | entry=${p['entry_price']:.4f} "
                    f"| current=${prices.get(p['coin'], 0):.4f} | PnL=${p['unrealized_pnl']:+.2f} "
                    f"({p['gain_loss_pct']:+.1f}%) | held {p['hold_hours']:.0f}h "
                    f"| TP=${p['tp_price']:.4f} SL=${p['sl_price']:.4f}"
                )
        else:
            context_lines.append("  No open positions")

        context_lines += ["", "=== SIGNALS ==="]
        for c in scored:
            if c.get("entry_ok") or c["score"] >= 40:
                context_lines.append(
                    f"  {c['coin']}: {c['score']}/100 {c['direction'].upper()} | "
                    f"RSI={c.get('rsi')} price=${c['price']:.2f}"
                )

        context_lines += ["", "=== FUNDING RATES (per 8h) ==="]
        for coin, fr in funding.items():
            context_lines.append(f"  {coin}: {fr:+.4f} ({'longs pay' if fr > 0 else 'shorts pay'})")

        context_lines += ["", "=== RISK STATE ===",
            f"  Win rate: {risk_state['recent_wins']}/{risk_state['recent_total']} | All clear: {risk_state['all_clear']}",
            f"  Drawdown: {'OK' if risk_state['drawdown_ok'] else 'LIMIT HIT'}",
        ]

        trade_usd = min(max(account_usd * TRADE_SIZE_PCT, MIN_TRADE_USD), MAX_TRADE_USD)
        context_lines += [
            "", "=== INSTRUCTIONS ===",
            f"Trade size: ${trade_usd:.0f} margin (= ${trade_usd * LEVERAGE:.0f} notional at {LEVERAGE}x)",
            "Decide: execute a trade, close a position, or take no action.",
            "Only trade if conviction ≥ 6/10. Borderline = no_action.",
        ]

        messages = [{"role": "user", "content": "\n".join(context_lines)}]
        model = "claude-haiku-4-5-20251001"

        try:
            response = self.client.messages.create(
                model=model,
                max_tokens=2048,
                system=SYSTEM_PROMPT_CACHED,
                tools=TOOLS,
                messages=messages,
            )
        except anthropic.APIStatusError as e:
            if "usage limits" in str(e) or "credit balance" in str(e):
                logger.error(f"HL Claude: API limit reached — {e}")
                return
            raise

        usage = response.usage
        cache_read  = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        if cache_read or cache_write:
            logger.info(f"HL cache: {cache_read} read, {cache_write} written")

        # Tool loop
        while response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = self._handle_tool(block.name, block.input, prices, account_usd)
                    tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": result})
                    logger.info(f"HL tool result: {result}")

            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})
            response = self.client.messages.create(
                model=model,
                max_tokens=2048,
                system=SYSTEM_PROMPT_CACHED,
                tools=TOOLS,
                messages=messages,
            )

        self._last_routine_ts = time.time()

        usage  = response.usage
        rates  = {"claude-haiku-4-5-20251001": (0.25, 1.25), "claude-sonnet-4-6": (3.0, 15.0)}
        ir, or_ = rates.get(model, (0.25, 1.25))
        cost   = (usage.input_tokens * ir + usage.output_tokens * or_) / 1_000_000
        cost_tracker.record_anthropic(cost, model)
        logger.info(f"HL Claude: {usage.input_tokens} in / {usage.output_tokens} out | ${cost:.4f}")

        for block in response.content:
            if hasattr(block, "text") and block.text:
                logger.info(f"HL Claude reasoning: {block.text[:500]}")

    def _handle_tool(self, name: str, tool_input: dict, prices: dict, account_usd: float) -> str:
        if name == "execute_trade":
            coin      = tool_input.get("coin", "").upper()
            direction = tool_input.get("direction", "")
            size_usd  = float(tool_input.get("size_usd", 0))
            confidence= int(tool_input.get("confidence", 0))
            reasoning = tool_input.get("reasoning", "")

            if confidence < 6:
                return f"Trade blocked: confidence {confidence}/10 < 6 minimum"
            if coin not in {"BTC", "ETH", "SOL"}:
                return f"Trade blocked: {coin} not in allowed coins"
            if coin in positions.get_open_positions():
                return f"Trade blocked: already have open position in {coin}"
            if size_usd < MIN_TRADE_USD or size_usd > MAX_TRADE_USD:
                size_usd = min(max(size_usd, MIN_TRADE_USD), MAX_TRADE_USD)

            price = prices.get(coin, 0)
            if not price:
                return f"Trade blocked: no price for {coin}"

            ok, reason = risk.can_open_trade(account_usd, len(positions.get_open_positions()), MAX_OPEN, coin)
            if not ok:
                return f"Trade blocked by risk guard: {reason}"

            pos = executor.open_position(coin, direction, size_usd, price, reasoning=reasoning)
            if pos:
                return f"Opened {direction.upper()} {coin}: ${size_usd:.0f} margin @ ${price:.2f}"
            return f"Failed to open {direction.upper()} {coin} — check executor logs"

        elif name == "close_position":
            coin   = tool_input.get("coin", "").upper()
            reason = tool_input.get("reason", "claude_discretionary")
            if coin not in positions.get_open_positions():
                return f"No open position in {coin}"
            realized = executor.close_position(coin, reason=reason)
            if realized:
                return f"Closed {coin}: P&L ${realized['gain_loss_usd']:+.2f} ({realized['gain_loss_pct']:+.1f}%)"
            return f"Failed to close {coin} — check executor logs"

        return f"Unknown tool: {name}"
