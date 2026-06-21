import os
import json
import httpx
import certifi
import anthropic
from datetime import datetime, timezone
from bot.config import ANTHROPIC_API_KEY, TOKENS, MAX_DEPLOY_USD, MIN_TRADE_USD, MAX_TRADE_USD, SWING_TARGETS
from bot.portfolio import Portfolio
from bot.executor import Executor
from bot.logger import setup_logger
from bot import history, wiki, positions, blacklist, token_cache, knowledge, risk, capital
from bot.cost_tracker import record_anthropic, get_summary as get_cost_summary
from bot.self_improve import run_self_analysis
from bot.screener import get_screening_report
from bot.signals import score_candidates as _score_candidates, check_held_positions as _check_held_positions
from bot.evaluator import score_coin, format_report
from bot.liquidity import filter_liquid_coins

logger = setup_logger("agent")

TRADE_BLOCKS_FILE = "data/trade_blocks.json"
_KEEP_BLOCKS = 100


def _log_trade_block(token_in: str, token_out: str, amount_usd: float, reason: str):
    """Persist agent-level trade rejections so the dashboard can surface them."""
    os.makedirs("data", exist_ok=True)
    try:
        blocks = json.load(open(TRADE_BLOCKS_FILE)) if os.path.exists(TRADE_BLOCKS_FILE) else []
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
    tmp = TRADE_BLOCKS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(blocks, f, indent=2)
    os.replace(tmp, TRADE_BLOCKS_FILE)


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


def _update_swing_target_ranges(prices: dict):
    """
    Update SWING_TARGETS weekly_range_low/high from stored price history.
    Uses last 7 days of price data (up to 336 data points at 30min intervals).
    Only updates if we have enough history — otherwise keeps existing values.
    """
    for sym, info in SWING_TARGETS.items():
        stored_prices = history.get_prices(sym)
        if len(stored_prices) < 20:
            continue
        # Use last 7 days worth of points (336 at 30min, cap at available)
        window = stored_prices[-336:] if len(stored_prices) >= 336 else stored_prices
        week_low  = round(min(window), 4)
        week_high = round(max(window), 4)
        if week_low != info.get("weekly_range_low") or week_high != info.get("weekly_range_high"):
            info["weekly_range_low"]  = week_low
            info["weekly_range_high"] = week_high
            logger.info(f"Updated {sym} weekly range: ${week_low}–${week_high}")


def _refresh_held_token_prices(prices: dict):
    """
    For every open position not in the main market feed, fetch a fresh price
    from CoinGecko and inject it directly into the prices dict for this tick.
    Also persists to token cache. Skips tokens already priced this cycle.
    """
    import requests, certifi
    open_pos = positions.get_open_positions()
    # Build symbol→cg_id map for tokens we need to price
    sym_to_cg = {}
    for sym, lots in open_pos.items():
        if sym in prices and prices[sym] > 0:
            continue  # already priced
        for lot in lots:
            cg_id = lot.get("cg_id", "")
            if cg_id:
                sym_to_cg[sym] = cg_id
                break
    if not sym_to_cg:
        return
    try:
        resp = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": ",".join(sym_to_cg.values()), "vs_currencies": "usd"},
            timeout=8,
            verify=certifi.where(),
        )
        resp.raise_for_status()
        data = resp.json()
        # Build reverse: cg_id → symbol
        cg_to_sym = {v: k for k, v in sym_to_cg.items()}
        for cg_id, price_data in data.items():
            price = price_data.get("usd", 0)
            if price > 0:
                sym = cg_to_sym.get(cg_id, "")
                # Inject into live prices dict so _execute_tool sees it directly
                if sym:
                    prices[sym] = price
                # Persist to token cache
                cached = token_cache.get(cg_id)
                if cached:
                    token_cache.store(
                        cg_id, cached["address"], cached["decimals"],
                        cached["name"], price, cached.get("symbol", "")
                    )
                logger.info(f"Refreshed price for {cg_id} ({sym}): ${price:.6f}")
    except Exception as e:
        logger.warning(f"Could not refresh held token prices: {e}")

SYSTEM_PROMPT = f"""You are a fully autonomous crypto trading agent on the Base blockchain. You operate without human supervision. Make all trading decisions independently using the data provided each tick.

## Your mission
Grow the portfolio through disciplined short-term trading. Protect capital first, grow it second. Every decision — entry, exit, position sizing — is yours to make.

## Capital management (dynamic — read from prompt each tick)
- Capital floor: a protected reserve you never touch. Displayed each tick.
- Max deploy: everything above the floor can be deployed across open positions.
- Trade size: 5–10% of total portfolio per trade (shown each tick).
- Always keep at least 0.002 ETH for gas — never trade it.
- Native ETH in the wallet cannot be swapped directly. Use wrap_eth to convert it to WETH first, then execute_swap WETH→token. Leave 0.002 ETH unwrapped for gas.
- Recovery mode: if USDC drops below the floor, hold cash, make NO new entries until recovered.

## Trading style
- Short-term swing trades: hours to 2–3 days maximum.
- Get in on a clear setup, capture the move, exit back to USDC. No long-term holds.
- Frequent small wins beat chasing big gains.
- Exit when the thesis breaks — do not hope for recovery.

## Entry setups (in order of reliability)

**Setup 1 — Momentum Continuation**
>2% positive 1h momentum, above-average volume, RSI below 70. The move has conviction.

**Setup 2 — Dip Recovery**
Down 5–15% in 24h but 1h momentum just turned positive. RSI below 50. Buyers stepping in.
Warning: if 1h stalls again, exit immediately — don't catch a falling knife.

**Setup 3 — Catalyst Trade**
Upcoming event in analyst notes (listing, partnership, emission change). Enter before, sell into the reaction.

**Setup 4 — TVL Rising**
Base protocol TVL up >5% in 24h with positive price momentum. Real capital inflow = adoption.

**Setup 5 — RSI Oversold Recovery**
RSI below 30 AND 1h momentum just turned positive. High conviction — use only on liquid tokens.

## Performance tier — what it controls
The tier (CONSERVE / CAUTIOUS / ACTIVE / AGGRESSIVE / FULL) affects scan interval and position sizing only.
It does NOT restrict which entry setups are valid. All Setups 1–5 are available in every tier.
The code enforces its own guards (win rate, drawdown, position cap). If you are executing a trade,
the guards already passed. Do NOT invent setup restrictions based on tier name or win rate history —
that is the code's job, not yours.

## Exits — use your judgment
- Sell into strength when momentum fades — don't wait for the full move.
- If the trade thesis breaks (momentum reversal, bad news), exit regardless of P&L.
- If a position has been open 2x its intended hold time with no move, exit and redeploy.
- ALWAYS sell back to USDC — never rotate directly between crypto assets.

## Position management — fully autonomous
- You are responsible for ALL open positions every tick.
- If a held token has no price feed (shows $0): call get_token_info to get the current price, then decide whether to hold or exit.
- If a position is significantly down and the thesis is broken: exit it. Do not wait for instructions.
- If a hold window has expired: evaluate current conditions and exit if momentum is gone.
- Stop losses are mechanical for known tokens. For tokens not in the registry, you must monitor and execute exits manually via execute_swap.

## When buying a token via get_token_info
Always pass token_out_cg_id in the execute_swap call so the position can be tracked and priced in future cycles.

## Swing trading targets
Fast in-out trades. Buy near weekly low, sell at TP, sit in USDC, repeat.
Use tight parameters: take_profit_pct=8, stop_loss_pct=8.

Current swing targets (updated ranges injected in market context each tick):
""" + "\n".join(
    f"- {sym}: {info['description']} | TP +{info['take_profit_pct']}% SL -{info['stop_loss_pct']}%"
    for sym, info in SWING_TARGETS.items()
) + """

## Token discovery
Trade ANY Base token. Use get_token_info(cg_id) before trading unlisted tokens.
Use evaluate_coin(cg_id) when uncertain about safety.
If either returns "rate limited" — skip that coin this tick, do not retry.

## Primary targets
- Base-native tokens with real projects, $5M–$2B market cap, real on-chain liquidity.
- WETH/cbBTC: only on clear short-term technical setups.
- Avoid: stablecoins, wrapped assets, tokens under 3 months old (size very small if you do).

## Pump and dump protection
- >20% spike with no news = likely pump — evaluate before touching.
- Volume/market cap ratio >5x = suspicious.
- MACD bearish crossover + RSI overbought during a spike = exit signal.

## Airdrop / dusting attack protection
Malicious tokens are sometimes sent to wallets uninvited to trick bots into interacting with them. Interacting with these tokens (approving, selling) can drain the wallet via malicious contracts.
- NEVER attempt to sell or approve a token you did not explicitly buy in this session.
- If a token appears in wallet holdings but has no open position in your records, ignore it completely.
- Do not include untracked wallet tokens in portfolio calculations or P&L.

## Knowledge base and self-improvement
- Read your accumulated knowledge base every tick — it contains real observations from past trades AND automated performance analysis.
- Entries marked [AUTO-ANALYSIS] are generated by the self-improvement engine — treat them as high-priority guidance.
- Entries marked [PERFORMANCE SUMMARY] show your recent win rate and risk-reward ratio — use them to calibrate confidence.
- After any notable observation (pattern, token behavior, market condition), call add_knowledge to save it.
- Before entering a token you've traded before, check the knowledge base for prior notes on it.
- You are expected to continuously improve. If you notice a pattern the system hasn't caught, save it.

## Risk guards (enforced in code — do not override)
- Gas cost >2% of trade size: trade blocked automatically.
- Daily drawdown >10%: no new trades until next day.
- Win rate <40% over last 10 trades: hold cash, reassess strategy.
- Position cap adapts to market regime: STRONG_BEAR=4, BEAR/NEUTRAL=7, BULL/STRONG_BULL=10. Current cap shown each tick.
- Stop-out cooldown: 30min no re-entry after being stopped out of a token.

## Confidence requirement (STRICTLY ENFORCED)
Every trade must include a confidence score (1-10) in execute_swap.
- 8-10: Strong signal, multiple confirming indicators, act decisively.
- 6-7: Reasonable setup, moderate conviction, acceptable to trade.
- 1-5: Unclear signal, weak setup, insufficient data. DO NOT TRADE. Hold cash instead.
Trades with confidence < 6 are automatically blocked. Be honest — if you're unsure, score low.

Always reason step by step. Act with conviction where warranted. You have full authority to manage this portfolio."""

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
                "reasoning":          {"type": "string",  "description": "Why you are making this trade"},
                "token_out_cg_id":    {"type": "string",  "description": "CoinGecko ID of token being bought (e.g. 'venice-token'). Always provide when buying a non-standard token so it can be tracked for future pricing."},
                "confidence":         {"type": "integer", "description": "Your conviction score 1-10. Trades with confidence < 6 are blocked. Be honest: 8-10=strong signal, 6-7=reasonable, 1-5=do not trade."},
            },
            "required": ["token_in", "token_out", "amount_usd", "reasoning", "confidence"],
        },
    },
    {
        "name": "wrap_eth",
        "description": (
            "Wrap native ETH in the wallet into WETH so it can be used in swaps. "
            "The wallet holds native ETH from gas refunds and initial funding that cannot be traded directly. "
            "Call this to convert ETH to WETH before executing a WETH→token swap. "
            "Check the portfolio ETH balance first; leave at least 0.002 ETH unwrapped for future gas."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "eth_amount": {"type": "number", "description": "Amount of ETH to wrap (e.g. 0.05). Leave 0.002 for gas."},
                "reasoning":  {"type": "string", "description": "Why you are wrapping ETH now."},
            },
            "required": ["eth_amount", "reasoning"],
        },
    },
    {
        "name": "add_knowledge",
        "description": (
            "Save a lasting observation to the knowledge base so it persists across runs. "
            "Use this when you notice a repeatable pattern, a token's quirky behavior, a market insight, "
            "or a warning about a risky asset. Categories: token, market, strategy, warning."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["token", "market", "strategy", "warning"],
                    "description": "token=specific coin notes, market=macro patterns, strategy=what works/doesn't, warning=red flags",
                },
                "content": {
                    "type": "string",
                    "description": "The observation to save. Be specific and actionable.",
                },
            },
            "required": ["category", "content"],
        },
    },
]


class TradingAgent:
    def __init__(self, portfolio: Portfolio, executor: Executor):
        self.portfolio    = portfolio
        self.executor     = executor
        self.current_tier = {"sonnet_threshold": 5.0, "always_sonnet": False, "label": "CONSERVE"}
        self.last_best_signal_score = 0  # set after each tick for adaptive interval
        self.client = anthropic.Anthropic(
            api_key=ANTHROPIC_API_KEY,
            http_client=httpx.Client(verify=certifi.where()),
        )

    def _build_market_prompt(self, snapshot: dict) -> str:
        # Open positions with P&L
        open_pos = positions.get_position_summary(snapshot.get("prices", {}))
        realized = positions.get_realized_summary()

        currently_deployed = sum(p["cost_basis_usd"] for p in open_pos)
        tier = self.current_tier

        # Market regime and session context — must be calculated first
        fg_val  = snapshot.get("fear_and_greed", {}).get("value", 50)
        btc_ind = history.get_indicators("cbBTC")
        regime  = history.get_market_regime(btc_ind, fg_val)
        session = history.get_session_context()

        open_count = len([p for p in positions.get_position_summary(snapshot.get("prices", {})) if p])
        trade_allowed, trade_block_reason = risk.can_open_trade(snapshot.get("total_usd", 0), open_count, regime_label=regime["regime"])
        risk_summary = risk.get_risk_summary(snapshot.get("total_usd", 0), open_count, regime["regime"])
        total_usd    = snapshot.get("total_usd", 0)
        usdc_usd     = snapshot.get("holdings", {}).get("USDC", {}).get("value_usd", 0)
        cap_summary  = capital.get_summary(total_usd, usdc_usd)
        dyn_max_deploy = cap_summary["max_deploy"]
        dyn_min_trade  = cap_summary["min_trade"]
        dyn_max_trade  = cap_summary["max_trade"]
        in_recovery    = cap_summary["in_recovery"]

        lines = [
            f"Total portfolio value: ${total_usd:,.2f}",
            f"Performance tier: {tier.get('label','?')} | Total P&L: ${tier.get('total_pnl',0):+.2f}",
            f"Capital floor: ${cap_summary['floor']:.2f} (base ${cap_summary['base_floor']:.0f} + ${cap_summary['locked_profit']:.2f} locked profit) | Withdrawable: ${cap_summary['withdrawable']:.2f}",
            f"Deployment: ${currently_deployed:.2f} deployed | Max deploy: ${dyn_max_deploy:.2f} (everything above floor) | Available: ${max(0, dyn_max_deploy - currently_deployed):.2f}",
            f"Trade size: ${dyn_min_trade:.0f}–${dyn_max_trade:.0f} for NEW buys only (5–10% of portfolio). NO minimum on sells — always exit a position regardless of size.",
            f"Recovery mode: {'YES — USDC below floor, no new trades' if in_recovery else 'No — trading permitted'}",
            f"",
            f"== MARKET REGIME: {regime['regime']} (score {regime['score']:+d}) ==",
            f"BTC: 1h {regime['btc_1h']:+.2f}% | 4h {regime['btc_4h']:+.2f}% | 24h {regime['btc_24h']:+.2f}% | F&G {regime['fear_greed']}/100",
            f"Guidance: {regime['guidance']}",
            f"",
            f"Trading session: {session['session']} ({session['hour_utc']:02d}:00 UTC)",
            f"{session['volume_note']}",
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

        # Signal deterioration warnings for held positions
        signal_suggestions = snapshot.get("_signal_exit_suggestions", [])
        if signal_suggestions:
            lines += ["", "Signal deterioration warnings (soft — evaluate whether thesis still holds):"]
            for s in signal_suggestions:
                lines.append(f"  - {s}")

        if open_pos:
            lines += ["", "Open positions (entry → now | P&L | age | TP/SL):"]
            for p in open_pos:
                hold_days = p['hold_days']
                hold_str  = f"{hold_days}d" if hold_days >= 1 else "<1d"
                age_warn  = " [LONG HOLD — reassess thesis]" if hold_days >= 5 else ""
                tp_price  = p.get('take_profit_price')
                sl_price  = p.get('stop_loss_price')
                tp_str = f"TP ${tp_price:,.4f}" if tp_price else "TP n/a"
                sl_str = f"SL ${sl_price:,.4f}" if sl_price else "SL n/a"
                lines.append(
                    f"  {p['symbol']}: {p['amount_tokens']:.6f} tokens | "
                    f"entry ${p['entry_price']:,.4f} | now ${p['current_price']:,.4f} | "
                    f"cost ${p['cost_basis_usd']:.2f} | value ${p['current_value']:.2f} | "
                    f"P&L ${p['gain_loss_usd']:+,.2f} ({p['gain_loss_pct']:+.2f}%) | "
                    f"held {hold_str} | {tp_str} | {sl_str}{age_warn}"
                )
                if p['gain_loss_pct'] <= -25:
                    lines.append(
                        f"    !! SELL IMMEDIATELY: down {p['gain_loss_pct']:.1f}% — execute_swap to exit full position. "
                        f"Minimum trade size does NOT apply to sells of existing positions. !!"
                    )
                elif p['gain_loss_pct'] >= 60:
                    lines.append(
                        f"    !! SELL NOW (TP-L3): up {p['gain_loss_pct']:.1f}% — execute_swap to sell 25% of this position. "
                        f"No minimum size restriction on exits. !!"
                    )
                elif p['gain_loss_pct'] >= 40:
                    lines.append(
                        f"    !! SELL NOW (TP-L2): up {p['gain_loss_pct']:.1f}% — execute_swap to sell 25% of this position. "
                        f"No minimum size restriction on exits. !!"
                    )
                elif p['gain_loss_pct'] >= 20:
                    lines.append(
                        f"    !! SELL NOW (TP-L1): up {p['gain_loss_pct']:.1f}% — execute_swap to sell 25% of this position. "
                        f"No minimum size restriction on exits. !!"
                    )

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

        # ATR-based candle indicators for key tracked tokens
        candle_ind = {sym: history.get_candle_indicators(sym)
                      for sym in ["VVV", "AERO", "WETH", "cbBTC"]}
        lines += ["", "Technical indicators (from stored price history):"]
        for sym, ind in indicators.items():
            n = ind["data_points"]
            if n < 2:
                lines.append(f"  {sym}: insufficient history ({n} data points — building up)")
                continue
            rsi   = f"RSI={ind['rsi_14']}" if ind["rsi_14"] else "RSI=n/a"
            trend = ind["trend"] or "n/a"
            m1h   = f"{ind['momentum_1h_pct']:+.2f}%" if ind["momentum_1h_pct"] is not None else "n/a"
            m4h   = f"{ind['momentum_4h_pct']:+.2f}%" if ind["momentum_4h_pct"] is not None else "n/a"
            m24h  = f"{ind['momentum_24h_pct']:+.2f}%" if ind["momentum_24h_pct"] is not None else "n/a"
            parts = [f"  {sym}: {rsi} | trend={trend} | 1h={m1h} | 4h={m4h} | 24h={m24h} ({n} pts)"]
            macd = ind.get("macd")
            if macd:
                parts.append(
                    f"    MACD: {macd['macd']:+.6f} | signal={macd['signal']:+.6f} | "
                    f"hist={macd['histogram']:+.6f} | {macd['crossover']} crossover"
                )
            bb = ind.get("bollinger_bands")
            if bb:
                squeeze_note = " [SQUEEZE — breakout likely]" if bb["squeeze"] else ""
                parts.append(
                    f"    BB: upper={bb['upper']:.4f} mid={bb['mid']:.4f} lower={bb['lower']:.4f} "
                    f"| width={bb['width_pct']:.1f}% | position={bb['position']:.2f} (0=lower,1=upper){squeeze_note}"
                )
            sr = ind.get("support_resistance")
            if sr:
                parts.append(
                    f"    S/R: support={sr['support']:.4f} (+{sr['dist_to_support_pct']:.1f}% away) | "
                    f"resistance={sr['resistance']:.4f} (-{sr['dist_to_resistance_pct']:.1f}% away)"
                )
            adx = ind.get("adx")
            if adx:
                parts.append(
                    f"    ADX: {adx['adx']:.1f} [{adx['regime']}] | "
                    f"DI+={adx['di_plus']:.1f} DI-={adx['di_minus']:.1f} | "
                    f"direction={adx['direction']}"
                )
            obv = ind.get("obv")
            if obv:
                div_note = f" *** {obv['divergence'].upper()} DIVERGENCE ***" if obv.get("divergence") else ""
                parts.append(f"    OBV: trend={obv['obv_trend']}{div_note}")
            lines.extend(parts)

        # Position correlation warnings
        if open_pos:
            held_syms = [p["symbol"] for p in open_pos if p.get("symbol") not in {"USDC","USDT","DAI"}]
            if len(held_syms) >= 2:
                corr_warnings = history.get_portfolio_correlations(held_syms)
                if corr_warnings:
                    lines += ["", "** CORRELATION WARNINGS — correlated positions amplify losses if market drops: **"]
                    for w in corr_warnings:
                        lines.append(f"  {w['sym_a']} & {w['sym_b']}: r={w['correlation']:.2f} [{w['risk']} correlation] — avoid adding more correlated exposure")

        # ATR volatility context for position sizing
        atr_lines = []
        for sym, ci in candle_ind.items():
            if ci and ci.get("atr_pct"):
                atr_lines.append(
                    f"  {sym}: ATR={ci['atr_pct']:.1f}% [{ci['regime']}] | "
                    f"Chandelier stop=${ci['chandelier_stop']:.4f}"
                )
        if atr_lines:
            lines += ["", "ATR volatility (use for position sizing — higher ATR = smaller size):"]
            lines.extend(atr_lines)

        # BTC trend context — most alts follow BTC direction
        btc_ind = indicators.get("cbBTC") or {}
        btc_md  = snapshot.get("market_data", {}).get("cbBTC", {})
        if btc_md or btc_ind.get("momentum_1h_pct") is not None:
            btc_1h  = btc_md.get("change_1h", btc_ind.get("momentum_1h_pct", 0)) or 0
            btc_24h = btc_md.get("change_24h", 0) or 0
            btc_trend = btc_ind.get("trend", "n/a")
            lines += [
                "",
                f"BTC market context: 1h {btc_1h:+.2f}% | 24h {btc_24h:+.2f}% | trend={btc_trend}",
                "Note: broad alt weakness often follows BTC drops — factor this into entry timing.",
            ]

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

        # Risk guard status
        streak = risk_summary.get("streak", "—")
        lines += ["",
            f"Risk guards: {'** BLOCKED ** — ' + trade_block_reason if not trade_allowed else 'OK — new trades permitted'}",
            f"Positions: {open_count}/{risk.MAX_OPEN_POSITIONS} open | Streak: {streak} | "
            f"Recent W/L: {risk_summary['recent_wins']}/{risk_summary['recent_total']}",
        ]

        # Manual catalyst notes
        notes = _read_notes()
        if notes:
            lines += ["", "Analyst notes / upcoming catalysts (from notes.txt):"]
            for note in notes:
                lines.append(f"  - {note}")

        # Swing targets with live range position — helps agent know if price is at top/bottom of range
        live_prices = snapshot.get("prices", {})
        swing_lines = []
        for sym, info in SWING_TARGETS.items():
            lo  = info.get("weekly_range_low", 0)
            hi  = info.get("weekly_range_high", 0)
            cur = live_prices.get(sym, 0)
            if hi > lo and cur > 0:
                pct_of_range = round((cur - lo) / (hi - lo) * 100)
                range_note = f"${lo}–${hi} | currently at {pct_of_range}% of 7d range"
            elif lo > 0 and hi > 0:
                range_note = f"${lo}–${hi} | live price unavailable"
            else:
                range_note = "7d range not yet calibrated"
            swing_lines.append(
                f"  {sym}: {info['description']} | {range_note} | "
                f"TP +{info['take_profit_pct']}% SL -{info['stop_loss_pct']}%"
            )
        if swing_lines:
            lines += ["", "Swing target ranges (buy near 0%, sell near 100%):"]
            lines.extend(swing_lines)

        # Persistent knowledge base — patterns and observations saved from prior runs
        kb = knowledge.get_summary()
        if kb and kb != "No knowledge entries yet.":
            lines += ["", "== KNOWLEDGE BASE (your accumulated observations) ==", kb]

        lines += [
            "",
            "Known tokens (no lookup needed — use symbol directly in execute_swap):",
            ", ".join(TOKENS.keys()),
            "For any other Base token: use get_token_info(cg_id) to get contract address, then trade it.",
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
                chg = g.get("change_24h", g.get("change", 0))
                lines.append(f"  {g['symbol']}: {chg:+.1f}% | cgid:{g['cg_id']}")

        # DeFiLlama — top 5 Base protocols with biggest TVL change only
        defillama = snapshot.get("defillama_base", [])
        if defillama:
            top_tvl = [p for p in defillama if p.get("symbol") and abs(p.get("change_1d") or 0) > 2][:5]
            if top_tvl:
                lines += ["", "Base protocols with significant TVL change:"]
                for p in top_tvl:
                    lines.append(f"  {p['name']} ({p['symbol']}): TVL ${p['tvl']/1e6:.0f}M | {p.get('change_1d', 0):+.1f}%")

        # Signal-scored candidates using real daily OHLCV (EMA50, RSI, ATR, dip, momentum)
        # OHLCV is cached 4h per token — first run is slow, subsequent calls are instant
        if base_coins:
            top_by_vol = sorted(base_coins, key=lambda c: c.get("volume_24h", 0), reverse=True)
            scored     = _score_candidates(top_by_vol, regime["regime"])
            passing    = [s for s in scored if s["signal"]["entry_ok"]]
            self.last_best_signal_score = scored[0]["signal"]["score"] if scored else 0

            if passing:
                lines += ["", "== SIGNAL-FILTERED OPPORTUNITIES (score ≥ 55/100) ==",
                          "These passed EMA trend + RSI + dip + momentum + macro + volatility filters.",
                          "Use signal stop_pct as stop_loss_pct and target_pct as take_profit_pct in execute_swap."]
                for s in passing:
                    sig = s["signal"]
                    stop_s   = f"{sig['stop_pct']}%" if sig['stop_pct'] else "n/a"
                    target_s = f"{sig['target_pct']}%" if sig['target_pct'] else "n/a"
                    lines.append(
                        f"  {s['symbol']}: score={sig['score']}/100 | RSI={sig['rsi']} | "
                        f"ATR={sig['atr_pct']}% | stop={stop_s} | target={target_s} (2:1 R/R) | "
                        f"cgid:{s['cg_id']}"
                    )
                    for cond in sig.get("conditions", []):
                        lines.append(f"    {cond}")
            else:
                best_score = scored[0]["signal"]["score"] if scored else 0
                lines += ["", f"Signal filter: no candidates scored ≥ 55 this tick — do not force entries.",
                          f"(Scored {len(scored)} candidates, best score: {best_score}/100)"]

        # Wiki only for tokens currently held (not entire registry)
        held_symbols = [sym for sym, h in snapshot.get("holdings", {}).items() if h.get("balance", 0) > 0.000001 and sym not in {"USDC","USDT","DAI","ETH"}]
        if held_symbols:
            wiki_text = wiki.get_all_summaries(held_symbols)
            if wiki_text:
                lines += ["", "Wiki for held tokens:", wiki_text]

        return "\n".join(lines)

    def _get_token_info(self, cg_id: str, live_prices: dict | None = None) -> str:
        # Sanitize cg_id — must be alphanumeric with hyphens only (CoinGecko ID format)
        import re
        if not cg_id or not re.match(r'^[a-z0-9\-]{1,80}$', cg_id):
            return f"Invalid CoinGecko ID format: '{cg_id}'. IDs must be lowercase alphanumeric with hyphens."

        # Check local cache for static info (contract address, decimals, name).
        # For price, always prefer the live snapshot price fetched at tick start —
        # the cache price can be days old and will cause the agent to misjudge setups.
        cached = token_cache.get(cg_id)
        if cached:
            symbol = cached.get("symbol", "")
            # Use the live tick price if we have it; fall back to a fresh CoinGecko fetch
            live_price = (live_prices or {}).get(symbol.upper(), 0) if symbol else 0
            if live_price > 0:
                cached["price"] = live_price
            logger.info(f"Token info from cache: {cg_id} @ ${cached.get('price', 0):,.6f} ({'live tick price' if live_price > 0 else 'cached'})")
            from bot.liquidity import check_base_liquidity
            liq = check_base_liquidity(
                w3=self.portfolio.w3,
                token_address=cached["address"],
                token_symbol=cached.get("symbol", cg_id),
                token_price_usd=cached.get("price", 0),
                token_decimals=cached.get("decimals", 18),
            )
            if not liq["liquid"]:
                return (
                    f"{cached['name']} ({cg_id}) FAILED LIQUIDITY CHECK — do NOT buy.\n"
                    f"Reason: {liq['reason']}\n"
                    f"Sell-side pool is too thin — buying this token risks being unable to exit "
                    f"without a major loss. Choose a different token."
                )
            return (
                f"{cached['name']} ({cg_id})\n"
                f"Base contract: {cached['address']}\n"
                f"Decimals: {cached['decimals']}\n"
                f"Price: ${cached.get('price', 0):,.6f}\n"
                f"Liquidity: PASSED ({liq['reason']})\n"
                f"Use these values in execute_swap."
            )

        import time as _time
        import requests as req
        _time.sleep(2.0)  # respect CoinGecko rate limit
        try:
            resp = req.get(
                f"https://api.coingecko.com/api/v3/coins/{cg_id}",
                params={"localization": "false", "tickers": "false"},
                timeout=15,
                verify=certifi.where(),
            )
            if resp.status_code == 429:
                return f"CoinGecko rate limited for {cg_id} — wait 60s and try again next tick"
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
            # Save to cache so we never need to call CoinGecko for this token again
            symbol_guess = data.get("symbol", "").upper()
            token_cache.store(cg_id, contract, decimals, name, price, symbol=symbol_guess)

            # Run GoPlus security check on any new token before the agent can buy it
            from bot.evaluator import check_goplus_security
            security = check_goplus_security(contract)
            sec_note = ""
            if security.get("checked"):
                if not security.get("safe"):
                    flags = "; ".join(security.get("flags", []))
                    return (
                        f"{name} ({cg_id}) FAILED SECURITY CHECK — do NOT buy.\n"
                        f"Flags: {flags}\n"
                        f"GoPlus honeypot={security.get('is_honeypot')} | "
                        f"sell_tax={security.get('sell_tax', 0):.0%}"
                    )
                sec_note = f"\nSecurity: PASSED GoPlus check (no honeypot, sell_tax={security.get('sell_tax', 0):.0%})"

            # Run sell-side liquidity check — a token that can't be exited safely is not worth buying
            from bot.liquidity import check_base_liquidity
            liq = check_base_liquidity(
                w3=self.portfolio.w3,
                token_address=contract,
                token_symbol=symbol_guess,
                token_price_usd=price,
                token_decimals=decimals,
            )
            if not liq["liquid"]:
                return (
                    f"{name} ({cg_id}) FAILED LIQUIDITY CHECK — do NOT buy.\n"
                    f"Reason: {liq['reason']}\n"
                    f"Sell-side pool is too thin — buying this token risks being unable to exit "
                    f"without a major loss. Choose a different token."
                )
            liq_note = f"\nLiquidity: PASSED ({liq['reason']})"

            return (
                f"{name} ({cg_id})\n"
                f"Base contract: {contract}\n"
                f"Decimals: {decimals}\n"
                f"Price: ${price:,.6f}"
                f"{sec_note}"
                f"{liq_note}\n"
                f"Use these values in execute_swap."
            )
        except Exception as e:
            return f"Could not fetch token info for {cg_id}: {e}"

    def _handle_tool(self, tool_name: str, tool_input: dict, snapshot: dict) -> str:
        if tool_name == "get_token_info":
            cg_id = tool_input.get("cg_id", "")
            logger.info(f"Fetching token info: {cg_id}")
            return self._get_token_info(cg_id, live_prices=snapshot.get("prices", {}))
        if tool_name == "evaluate_coin":
            cg_id = tool_input.get("cg_id", "")
            logger.info(f"Evaluating coin: {cg_id}")
            evaluation = score_coin(cg_id)
            return format_report(evaluation)
        if tool_name == "add_knowledge":
            cat     = tool_input.get("category", "market")
            content = tool_input.get("content", "")
            logger.info(f"Saving knowledge [{cat}]: {content[:80]}")
            return knowledge.add_entry(cat, content)
        if tool_name == "wrap_eth":
            eth_amount = float(tool_input.get("eth_amount", 0))
            reasoning  = tool_input.get("reasoning", "")
            if eth_amount <= 0:
                return "wrap_eth: eth_amount must be > 0"
            logger.info(f"Wrapping {eth_amount:.6f} ETH → WETH | {reasoning}")
            try:
                tx_hash = self.executor.wrap_eth(eth_amount)
                return f"Wrapped {eth_amount:.6f} ETH → WETH successfully | tx {tx_hash[:16] if tx_hash else 'None'}"
            except Exception as e:
                return f"wrap_eth failed: {e}"
        return self._execute_tool(tool_input, snapshot)

    def _execute_tool(self, tool_input: dict, snapshot: dict) -> str:
        token_in_sym  = tool_input["token_in"].upper()
        token_out_sym = tool_input["token_out"].upper()
        amount_usd    = float(tool_input["amount_usd"])
        reasoning     = tool_input.get("reasoning", "")
        confidence    = int(tool_input.get("confidence", 5))

        logger.info(f"Agent decision: {token_in_sym} → {token_out_sym} | ${amount_usd:.2f} | confidence={confidence}/10 | {reasoning}")

        # Confidence gate — block low-conviction trades when buying
        is_buy = token_in_sym in {"USDC", "USDT", "DAI"}
        if is_buy and confidence < 6:
            logger.info(f"Trade blocked: confidence {confidence}/10 below threshold (6). Holding cash.")
            return f"Trade skipped: confidence score {confidence}/10 is below the minimum threshold of 6. Conditions are unclear — holding cash is the right call."

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

        # Get price for token_in — check main prices dict first, then token cache for custom tokens
        price_in = snapshot["prices"].get(token_in_sym, 0)
        if price_in == 0 and token_in_sym not in {"USDC","USDT","DAI"}:
            cached_tok = token_cache.get_by_symbol(token_in_sym)
            if cached_tok and cached_tok.get("price", 0) > 0:
                price_in = cached_tok["price"]
                logger.info(f"Using token cache price for {token_in_sym}: ${price_in:.6f}")
            else:
                return f"Could not get price for {token_in_sym} — run get_token_info first"
        if token_in_sym in {"USDC","USDT","DAI"}:
            price_in = 1.0

        # Convert USD amount to token units
        amount_tokens = amount_usd / price_in
        amount_wei = int(amount_tokens * (10 ** token_in["decimals"]))

        # Check blacklist
        if blacklist.is_blocked(token_out_sym):
            _log_trade_block(token_in_sym, token_out_sym, amount_usd, f"{token_out_sym} is on the block list")
            return f"{token_out_sym} is on your block list — trade cancelled"

        # Dynamic capital limits
        _total   = snapshot.get("total_usd", 0)
        _usdc    = snapshot.get("holdings", {}).get("USDC", {}).get("value_usd", 0)
        _cap     = capital.get_summary(_total, _usdc)
        _min_t   = _cap["min_trade"]
        _max_t   = _cap["max_trade"]
        _max_dep = _cap["max_deploy"]

        # Recovery mode — no new buys
        if token_in_sym in {"USDC", "USDT", "DAI"} and _cap["in_recovery"]:
            msg = f"Recovery mode: USDC reserve ${_usdc:.2f} is below floor ${_cap['floor']:.2f} — no new trades until recovered"
            _log_trade_block(token_in_sym, token_out_sym, amount_usd, msg)
            return msg

        # Stop-out cooldown — no re-entry into recently stopped-out tokens
        if token_in_sym in {"USDC", "USDT", "DAI"}:
            ok, cooldown_reason = risk.check_stopout_cooldown(token_out_sym)
            if not ok:
                _log_trade_block(token_in_sym, token_out_sym, amount_usd, cooldown_reason)
                return cooldown_reason

        # Enforce minimum trade size — buys only; never block exits of existing positions
        is_buy = token_in_sym in {"USDC", "USDT", "DAI"}
        if is_buy and amount_usd < _min_t:
            msg = f"Trade too small: ${amount_usd:.2f} is below minimum ${_min_t:.2f}"
            _log_trade_block(token_in_sym, token_out_sym, amount_usd, msg)
            return msg

        # Enforce maximum single trade size
        if amount_usd > _max_t:
            amount_usd = _max_t
            logger.info(f"Trade capped at ${_max_t:.0f} (dynamic max trade size)")

        # Enforce total deployment cap
        open_pos = positions.get_position_summary(snapshot.get("prices", {}))
        currently_deployed = sum(p["cost_basis_usd"] for p in open_pos)
        if token_in_sym in {"USDC", "USDT", "DAI"}:  # buying crypto
            if currently_deployed + amount_usd > _max_dep:
                remaining = _max_dep - currently_deployed
                if remaining < _min_t:
                    msg = f"Deployment cap reached: ${currently_deployed:.2f} deployed of ${_max_dep:.2f} max (floor: ${_cap['floor']:.2f})"
                    _log_trade_block(token_in_sym, token_out_sym, amount_usd, msg)
                    return msg
                amount_usd = min(amount_usd, remaining)
                logger.info(f"Trade reduced to ${amount_usd:.2f} to stay within dynamic cap ${_max_dep:.2f}")

        # Recalculate amount_wei after any cap adjustments
        amount_tokens = amount_usd / price_in
        amount_wei = int(amount_tokens * (10 ** token_in["decimals"]))

        # Check available balance.
        # Buys: cap at 98% of holdings to prevent rounding over-spend.
        # Sells: allow up to 105% of recorded value — price ticks between valuation
        # and execution, and full-position sells must always be allowed through.
        holding = snapshot["holdings"].get(token_in_sym, {})
        available_usd = holding.get("value_usd", 0)
        limit = available_usd * 1.05 if is_buy is False else available_usd * 0.98
        if amount_usd > limit:
            msg = f"Insufficient balance: have ${available_usd:.2f} of {token_in_sym}, need ${amount_usd:.2f}"
            _log_trade_block(token_in_sym, token_out_sym, amount_usd, msg)
            return msg

        # Get token_out price — check prices dict, market_data, and token cache
        token_out_price = snapshot["prices"].get(token_out_sym, 0)
        if token_out_price == 0:
            md = snapshot.get("market_data", {}).get(token_out_sym, {})
            token_out_price = md.get("price", 0)
        if token_out_price == 0:
            cached = token_cache.get(tool_input.get("token_out_address", "").lower() if tool_input.get("token_out_address") else "")
            if cached:
                token_out_price = cached.get("price", 0)

        tx_hash = self.executor.swap(
            token_in_address=token_in["address"],
            token_in_symbol=token_in_sym,
            token_in_decimals=token_in["decimals"],
            token_out_address=token_out["address"],
            token_out_symbol=token_out_sym,
            amount_in_wei=amount_wei,
            price_eth_usd=snapshot["prices"].get("WETH", 0),
            token_in_price_usd=price_in,
            token_out_price_usd=token_out_price,
            take_profit_pct=float(tool_input.get("take_profit_pct", 25.0)),
            stop_loss_pct=float(tool_input.get("stop_loss_pct", 25.0)),
            entry_reasoning=reasoning if token_in_sym in {"USDC", "USDT", "DAI"} else "",
            exit_reasoning=reasoning if token_in_sym not in {"USDC", "USDT", "DAI"} else "",
            cg_id=tool_input.get("token_out_cg_id", ""),
        )

        if tx_hash:
            return f"Swap submitted: {tx_hash}"
        return "Swap failed — check logs"

    def _is_market_active(self, snapshot: dict) -> bool:
        """
        Decide whether to use Sonnet (full reasoning) or Haiku (cheap scan).
        Sonnet is only used when there is something concrete to reason about —
        a qualifying signal, a position in trouble, or a strong intraday move.
        F&G and RSI extremes on registry tokens are NOT sufficient on their own.
        """
        fg = snapshot.get("fear_and_greed", {}).get("value", 50)

        # Always Sonnet if tier requires it (high profit mode)
        if self.current_tier.get("always_sonnet"):
            return True

        btc_ind      = history.get_indicators("cbBTC")
        regime       = history.get_market_regime(btc_ind, fg)
        regime_label = regime["regime"]
        threshold    = self.current_tier.get("sonnet_threshold", 5.0)
        market_data  = snapshot.get("market_data", {})
        open_pos     = positions.get_position_summary(snapshot.get("prices", {}))

        # Bear market: only Sonnet if a position is in genuine crisis (>30% P&L move)
        if regime_label in ("STRONG_BEAR", "BEAR"):
            for p in open_pos:
                if abs(p.get("gain_loss_pct", 0)) >= 30:
                    logger.info(f"Market active ({regime_label} override): {p['symbol']} P&L={p['gain_loss_pct']:+.1f}%")
                    return True
            logger.info(f"{regime_label} regime — using Haiku (no positions in crisis)")
            return False

        # Near-qualifying signal — worth Sonnet reasoning (score set by _build_market_prompt)
        best_score = getattr(self, "last_best_signal_score", 0)
        if best_score >= 45:
            logger.info(f"Market active: best signal score {best_score}/100")
            return True

        # Open position needs attention (±20% P&L)
        for p in open_pos:
            if abs(p.get("gain_loss_pct", 0)) >= 20:
                logger.info(f"Market active: {p['symbol']} P&L={p['gain_loss_pct']:+.1f}%")
                return True

        # Open position held >3 days needs a proper review
        for p in open_pos:
            if p.get("hold_days", 0) >= 3:
                logger.info(f"Market active: {p['symbol']} held {p['hold_days']}d — review needed")
                return True

        # Strong intraday price move on any tracked coin
        for sym, d in market_data.items():
            if abs(d.get("change_1h", 0)) >= threshold:
                logger.info(f"Market active: {sym} moved {d['change_1h']:+.1f}% (threshold {threshold}%)")
                return True

        # Urgent analyst note
        if any(n.upper().startswith("[URGENT]") for n in _read_notes()):
            logger.info("Market active: urgent analyst note")
            return True

        logger.info("Haiku: no signals ≥45, no positions in crisis, no strong moves")
        return False

    def run_once(self):
        logger.info("Agent tick: fetching portfolio snapshot...")
        snapshot = self.portfolio.get_snapshot()
        context = self.portfolio.market.get_full_context()
        snapshot["fear_and_greed"] = context["fear_and_greed"]
        snapshot["trending_tokens"] = context["trending_tokens"]
        snapshot["market_data"] = context["market_data"]
        snapshot["aerodrome_pools"] = context.get("aerodrome_pools", [])

        # Refresh live prices for any held token not in the main market feed.
        # Inject into snapshot["prices"] — that's what _execute_tool reads.
        # Also inject into context["prices"] so the LLM prompt sees them.
        _refresh_held_token_prices(snapshot["prices"])
        for sym, price in snapshot["prices"].items():
            if sym not in context["prices"]:
                context["prices"][sym] = price

        # Record prices to build up history for technical indicators
        history.record_prices(context["prices"])

        # Auto-update swing target weekly ranges from price history
        _update_swing_target_ranges(context["prices"])

        # Record portfolio value for drawdown tracking (once per day)
        risk.record_portfolio_value(snapshot.get("total_usd", 0))

        # Self-improvement: analyse closed trades and write insights to knowledge base
        run_self_analysis()

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
                # Fall back to token cache for tokens bought via get_token_info
                if not token_info:
                    cached = token_cache.get_by_symbol(sym)
                    if cached:
                        token_info = {
                            "address":  cached["address"],
                            "decimals": cached["decimals"],
                            "symbol":   sym,
                        }
                usdc_info  = TOKENS["USDC"]
                if token_info:
                    amount_wei = int(amt * (10 ** token_info["decimals"]))
                    price = context["prices"].get(sym, 0)
                    if price == 0:
                        cached = token_cache.get_by_symbol(sym)
                        if cached:
                            price = cached.get("price", 0)
                    if price <= 0:
                        logger.error(f"Mechanical exit {sym}: no price available, skipping this tick")
                        continue
                    tx = self.executor.swap(
                        token_in_address=token_info["address"],
                        token_in_symbol=sym,
                        token_in_decimals=token_info["decimals"],
                        token_out_address=usdc_info["address"],
                        token_out_symbol="USDC",
                        amount_in_wei=amount_wei,
                        token_in_price_usd=price,
                        token_out_price_usd=1.0,
                        exit_reasoning=f"Mechanical {ex['exit_type']}: {reason}",
                    )
                    # Raise TP only if the swap was submitted on-chain (tx is not None).
                    # If executor blocked pre-chain (tx=None), keep TP unchanged so the
                    # bot retries next tick — don't permanently strand the position.
                    if ex["exit_type"] == "take_profit" and tx is not None:
                        positions.raise_take_profit(sym, ex.get("lot_id", ""), multiplier=1.5)

        # Signal-based exit suggestions for held positions (replaces time window)
        fg_val_pre  = context.get("fear_and_greed", {}).get("value", 50)
        btc_ind_pre = history.get_indicators("cbBTC")
        regime_pre  = history.get_market_regime(btc_ind_pre, fg_val_pre)
        held_exits  = _check_held_positions(
            positions.get_open_positions(), snapshot["prices"], regime_pre["regime"]
        )
        snapshot["_signal_exit_suggestions"] = [ex["reason"] for ex in held_exits]

        # Discover new opportunities
        screening = get_screening_report()

        # Filter out blacklisted tokens
        def not_blocked(coin: dict) -> bool:
            return not blacklist.is_blocked(
                coin.get("symbol", ""), coin.get("cg_id", "")
            )

        base_raw = [c for c in screening.get("base_ecosystem", []) if not_blocked(c)]

        # Filter to tokens with real Base liquidity (cached — only checks each token once/day)
        w3 = self.portfolio.w3
        base_liquid = filter_liquid_coins(w3, base_raw, context["prices"])

        snapshot["base_ecosystem"] = base_liquid
        snapshot["top_gainers"]    = [c for c in screening.get("top_gainers_24h", []) if not_blocked(c) and c.get("liquidity_verified", True)]
        snapshot["defillama_base"] = screening.get("defillama_base", [])

        # Cache screener results and build agent watchlist for dashboard
        import json
        from datetime import datetime as _dt
        os.makedirs("data", exist_ok=True)

        base_eco = screening.get("base_ecosystem", [])
        prices   = context["prices"]
        fg_val   = context["fear_and_greed"].get("value", 50)
        all_inds = history.get_all_indicators([s for s in TOKENS if s not in {"USDC","USDT","DAI"}])

        # Build top 20 watchlist: score each coin by signal strength
        watchlist_coins = []
        for coin in base_eco[:30]:
            sym        = coin.get("symbol", "")
            change_1h  = coin.get("change_1h", 0) or 0
            change_24h = coin.get("change_24h", 0) or 0
            mcap       = coin.get("market_cap", 0) or 0
            vol        = coin.get("volume_24h", 0) or 0
            vol_mcap   = (vol / mcap) if mcap > 0 else 0

            signals = []
            if change_1h > 2:    signals.append(f"+{change_1h:.1f}% 1h momentum")
            if change_1h < -3:   signals.append(f"{change_1h:.1f}% 1h dip — watch for recovery")
            if change_24h > 10:  signals.append(f"+{change_24h:.1f}% 24h gainer")
            if vol_mcap > 0.5:   signals.append(f"High volume ({vol_mcap:.1f}x mcap)")
            ind = all_inds.get(sym, {})
            rsi = ind.get("rsi_14")
            if rsi and rsi < 30: signals.append(f"RSI oversold ({rsi:.0f})")
            if rsi and rsi > 70: signals.append(f"RSI overbought ({rsi:.0f})")
            is_trending = sym in [t.get("symbol","") for t in context.get("trending_tokens", [])]
            if is_trending:      signals.append("Trending on CoinGecko")

            watchlist_coins.append({
                "symbol":     sym,
                "name":       coin.get("name", ""),
                "price":      coin.get("price", 0),
                "change_1h":  change_1h,
                "change_24h": change_24h,
                "market_cap": mcap,
                "volume_24h": vol,
                "cg_id":      coin.get("cg_id", ""),
                "signals":    signals,
                "signal_count": len(signals),
                "blocked":    blacklist.is_blocked(sym, coin.get("cg_id","")),
            })

        # Sort by signal count desc, then 1h change
        watchlist_coins.sort(key=lambda c: (-c["signal_count"], -c["change_1h"]))

        # Only write cache when we have results — preserve last good watchlist on screener failures
        if watchlist_coins:
            with open("data/screener_cache.json", "w") as _f:
                json.dump({
                    "base_ecosystem": base_eco,
                    "top_gainers":    screening.get("top_gainers_24h", []),
                    "watchlist":      watchlist_coins[:20],
                    "updated":        _dt.utcnow().isoformat(),
                    "fear_greed":     fg_val,
                }, _f)

        market_context = self._build_market_prompt(snapshot)

        # Two-tier model: Haiku for quiet markets, Sonnet when signals are active
        active = self._is_market_active(snapshot)

        # Budget guard — force Haiku when >80% of monthly budget is consumed
        budget = float(os.getenv("ANTHROPIC_BUDGET_USD", "30"))
        spent  = get_cost_summary().get("anthropic_month", 0)
        if budget > 0 and spent >= budget * 0.8:
            if active:
                logger.warning(f"Budget guard: {spent:.2f}/{budget:.2f} spent — downgrading Sonnet → Haiku")
            active = False

        model = "claude-sonnet-4-6" if active else "claude-haiku-4-5-20251001"
        logger.info(f"Using model: {model} (market active: {active}, monthly: ${spent:.2f}/${budget:.2f})")

        # Skip Claude entirely when there is nothing actionable:
        # no open positions to manage + no signal near the entry threshold.
        # The screener and signal scoring still ran (dashboard needs them),
        # but there is no decision to make so the API call adds zero value.
        open_positions = positions.get_open_positions()
        has_open = any(lots for lots in open_positions.values())
        best_score = self.last_best_signal_score
        if not has_open and best_score < 45:
            logger.info(
                f"No positions + best signal {best_score}/100 — skipping Claude API call. "
                f"Resuming when score ≥ 45 or a position opens."
            )
            return

        messages = [{"role": "user", "content": market_context}]

        try:
            response = self.client.messages.create(
                model=model,
                max_tokens=2048 if not active else 4096,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=messages,
            )
        except anthropic.APIStatusError as e:
            if "usage limits" in str(e) or "credit balance" in str(e):
                logger.error(f"Anthropic API limit reached — pausing Claude calls: {e}")
                import json as _json
                os.makedirs("data", exist_ok=True)
                with open("data/credit_alert.json", "w") as _f:
                    _json.dump({"ts": datetime.now(timezone.utc).isoformat(), "active": True}, _f)
                return  # skip this tick; next tick will retry after the interval
            raise

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
        rates = {"claude-sonnet-4-6": (3.0, 15.0), "claude-haiku-4-5-20251001": (0.25, 1.25)}
        ir, or_ = rates.get(model, (3.0, 15.0))
        cost = (input_tokens * ir + output_tokens * or_) / 1_000_000
        logger.info(f"Tokens: {input_tokens} in / {output_tokens} out | Est. cost: ${cost:.4f}")
        record_anthropic(cost, model)

        # Log final reasoning — strip emoji that crash Windows cp1252 terminal
        for block in response.content:
            if hasattr(block, "text"):
                safe = block.text.encode("ascii", errors="replace").decode("ascii")
                logger.info(f"Agent reasoning: {safe}")
