# CryptoBot — Architecture Reference

Autonomous crypto trading bot on Base blockchain. Uses Claude AI as decision engine. Deployed on DigitalOcean (143.198.37.28). Deploy: `ssh root@143.198.37.28 'bash /opt/cryptobot/app/deploy/update.sh'`

**Before shipping any change, run the pre-flight checklist in [RULES.md](RULES.md).** That file contains the invariants — rules that must always hold. If a change would break one, fix the violation first.

---

## Critical: Two Prices Dicts — Never Confuse Them

`run_once()` builds **two separate dict objects** from two separate API calls:

- **`snapshot["prices"]`** — built by `portfolio.get_snapshot()` → `market.get_all_prices()`. This is what `_execute_tool()` reads when executing swaps. Registry tokens only until `_refresh_held_token_prices()` injects custom tokens into it.
- **`context["prices"]`** — built by `market.get_full_context()`. Used to build the LLM prompt. Custom token prices are synced in from `snapshot["prices"]` after the refresh.

**The refresh flow (agent.py run_once lines 867-876):**
```python
_refresh_held_token_prices(snapshot["prices"])   # injects into snapshot — this is what executor reads
for sym, price in snapshot["prices"].items():    # sync to context so LLM sees them too
    if sym not in context["prices"]:
        context["prices"][sym] = price
```

If you ever move the refresh call, make sure it still injects into `snapshot["prices"]` first.

---

## Token Registry vs Custom Tokens

**Registry** (`bot/config.py` TOKENS): WETH, USDC, USDT, DAI, cbBTC, cbETH. Hardcoded contract addresses. Always priced by `market.get_all_prices()`.

**Custom tokens**: Everything else (PENDLE, W, VIRTUAL, ZORA, STG, etc.). Discovered at runtime via `get_token_info(cg_id)`. Must have `cg_id` stored in the position lot so `_refresh_held_token_prices()` can price them each tick.

**The cg_id chain**: agent calls `execute_swap(token_out_cg_id=...)` → executor calls `positions.open_position(..., cg_id=...)` → lot stored in positions.json with cg_id → `_refresh_held_token_prices()` reads cg_id from lot, fetches price from CoinGecko, injects into `snapshot["prices"]`.

If a custom token has no cg_id in its lot, it can never be priced and the bot cannot exit it via normal flow.

---

## Dashboard Price-Building Pipeline

Order of precedence (dashboard.py `index()` ~line 770):

1. `market.get_all_prices()` — live prices for registry tokens
2. Live CoinGecko fetch — for custom tokens with known cg_id (from token_cache)
3. Token cache fallback — **only prices ≤4 hours old**. Older entries reflect purchase-time prices and will overstate portfolio value.

**Never fall back to `cost_basis_usd` for portfolio total** — that's what was paid, not current value. If price is unknown, exclude the position from total.

The `_is_cache_fresh()` helper enforces the 4h staleness limit. The `cached_at` field in token_cache entries is written by `token_cache.store()` in `"%Y-%m-%dT%H:%M:%SZ"` format (UTC, no tz suffix — handle with `.replace("Z", "+00:00")`).

---

## positions.json — All Write Sites

This is the most critical data file. A corrupt write loses all position records.

All writes use atomic write-then-rename (`os.replace`) via `positions._save()`. Direct writes in `main.py` also use the `.tmp` + `os.replace` pattern.

Write sites:
| Function | File | Trigger |
|----------|------|---------|
| `open_position()` | positions.py | Successful buy swap |
| `close_position()` | positions.py | Successful sell swap |
| `raise_take_profit()` | positions.py | After partial TP exit |
| `update_trailing_stops()` | positions.py | Each tick if position up >8% |
| `_backfill_position_cg_ids()` | main.py | Startup only |
| `_adjust_positions_for_downtime()` | main.py | Startup only, if offline >2h |

---

## Mechanical vs AI Exits

**Mechanical** (runs first, no AI reasoning, `positions.check_mechanical_exits()`):
- Stop loss: price ≤ stop_loss_price → sell 100% immediately
- Take profit: price ≥ take_profit_price → sell 50%, raise remaining TP by 1.5x
- Hold window: expired → soft suggestion to agent (not forced)

**AI** (runs after mechanical, agent decides via `execute_swap` tool):
- Agent sees all positions with P&L, hold days, TP/SL levels in prompt
- Can exit any position at any time with reasoning
- Exit reasoning recorded to knowledge base

---

## Key Risk Guards (bot/risk.py)

All enforced in code — agent cannot override:
- Daily drawdown > 10% → halt new trades
- Win rate < 40% over last 10 trades → halt new trades  
- Position cap: scales with portfolio size (7/10/15/20) × regime multiplier (0.70–1.0)
- Stop-out cooldown: 30min no re-entry after SL hit
- Recovery mode: no new trades if USDC < capital floor

---

## Capital Management (bot/capital.py)

- **Floor** = $100 base + 10% of every realized profit, locked permanently. Never shrinks.
- **Max deploy** = total portfolio - floor
- **Trade size** = 5–10% of portfolio, clamped $20–$100 (testing phase)
- **Recovery mode** = USDC balance < floor → no new trades

---

## Downtime Detection (main.py)

On every startup, `_adjust_positions_for_downtime()` checks `data/last_tick.json`. If bot was offline >2h, extends all position `max_hold_until` by the downtime gap (max 7 days). Prevents agent treating positions as immediately overdue.

`_record_tick()` is called:
1. Immediately after applying any hold window extension (so crash-restart loops don't re-extend)
2. After every successful `agent.run_once()` call

---

## Dusting Attack Protection (executor.py)

Before any sell, checks `positions.get_open_positions()`. If the token being sold isn't in open_positions, the swap is refused. Malicious tokens are sometimes airdropped to trigger draining contracts.

---

## Data Files

| File | Purpose |
|------|---------|
| `data/positions.json` | Open position lots (FIFO). Source of truth. |
| `data/token_cache.json` | CoinGecko token info + last known price + cached_at timestamp |
| `data/capital.json` | Floor + locked profit |
| `data/last_tick.json` | Timestamp of last successful tick (downtime detection) |
| `data/portfolio_snapshots.json` | Daily portfolio values (drawdown tracking, 30 days) |
| `data/stopout_cooldowns.json` | Stop-out timestamps per token (30min cooldown) |
| `data/knowledge.json` | Persistent agent observations (token, market, strategy, warning) |
| `data/screener_cache.json` | Last watchlist from screener (never overwritten with empty) |
| `records/realized_gains.csv` | Closed trades with P&L |
| `records/transactions.csv` | All swap attempts with gas, slippage, status |
| `wiki/*.md` | Auto-generated token notes (one per traded token) |

---

## Self-Improvement Engine (bot/self_improve.py)

`run_self_analysis()` runs every tick but only writes when new trades have closed since last run (tracked by trade count in `data/self_analysis.json`). Requires ≥3 closed trades total. Writes `[AUTO-ANALYSIS]` and `[PERFORMANCE SUMMARY]` entries to the strategy knowledge base. Agent also writes directly via `add_knowledge` tool during ticks when it notices patterns.

---

## Market Regime

`history.get_market_regime(btc_indicators, fear_greed_value)` returns one of: STRONG_BEAR, BEAR, NEUTRAL, BULL, STRONG_BULL. Calculated from BTC RSI + Fear & Greed index. Used by:
- Position cap (risk.py)
- Agent model selection (Haiku in STRONG_BEAR unless positions need attention)
- Agent prompt context

Regime **must be calculated before** risk calls in `_build_market_prompt()` — it's a parameter to `can_open_trade()` and `get_risk_summary()`.

---

## Model Tier Selection

`_is_market_active(snapshot)` decides Sonnet vs Haiku per tick:
- STRONG_BEAR + no urgent positions → Haiku (saves ~12x on API costs)
- Any position at ±15% or TP/SL zone → Sonnet regardless of regime
- All other conditions → Sonnet

Performance tier (bot/performance.py) controls tick interval: CONSERVE=15min, STANDARD=5min, AGGRESSIVE=2min.

---

## Known Gotchas

1. **snapshot vs context prices are different objects** — see top of this file. Don't pass context["prices"] where snapshot["prices"] is needed.
2. **Dashboard cg_prices fallback uses cached_at filter** — entries older than 4h excluded to prevent purchase-time prices inflating portfolio total.
3. **Custom token cg_id is critical** — without it, the token can never be priced or exited cleanly. Always pass `token_out_cg_id` in execute_swap when buying custom tokens.
4. **Regime must be set before risk calls** — `_build_market_prompt()` calculates regime at the top of the function before any risk.can_open_trade() or risk.get_risk_summary() calls.
5. **Partial TP raises the remaining lot's TP** — after selling 50% at TP, `raise_take_profit()` multiplies remaining lot's TP by 1.5x to prevent re-trigger next tick.
6. **Last tick must be recorded inside adjust_for_downtime** — so crash-restart loops don't apply the same extension N times before run_once() succeeds.
7. **positions.json writes are atomic** — always use `_save()` in positions.py (which does write-to-tmp + os.replace). Direct writes in main.py also use os.replace. Never use bare open+write.
