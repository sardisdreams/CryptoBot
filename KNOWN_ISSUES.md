# CryptoBot — Known Issues & Lessons Learned

This file is the authoritative record of every bug found in this bot.
**Read this before making any code change.** Each entry has:
- What broke and what the symptom was
- Why it broke (the root cause pattern)
- The invariant that must hold going forward

Claude Code reads this file at the start of each session and during code reviews.
The daily audit script checks for regressions on every pattern listed here.

---

## Category: API Cost / Rate Limiting

### 1. Prompt caching not implemented (missed 57% savings)
**Symptom:** Anthropic emailed about low prompt cache hit rate. $25 in 8 days.  
**Root cause:** System prompt and tools were passed as plain strings — cache_control never attached.  
**Fix:** `SYSTEM_PROMPT_CACHED` wraps prompt as a content block with `cache_control: {type: ephemeral, ttl: 1h}`. Last TOOLS entry also has `cache_control`.  
**Invariant:** Every `client.messages.create()` call must pass `system=SYSTEM_PROMPT_CACHED`, not the bare `SYSTEM_PROMPT` string. No exceptions.

### 2. Tool follow-up calls bypassed cache and upgraded model
**Symptom:** After a tool call (e.g. execute_swap), the follow-up response was using bare SYSTEM_PROMPT and hardcoded `claude-sonnet-4-6` even if the initial call used Haiku.  
**Root cause:** The agentic loop's follow-up `messages.create()` was hardcoded with `system=SYSTEM_PROMPT` and `model="claude-sonnet-4-6"`.  
**Fix:** Follow-up now inherits `model` variable and uses `SYSTEM_PROMPT_CACHED`.  
**Invariant:** All `messages.create()` calls in the agentic loop — both initial and follow-up — must use `SYSTEM_PROMPT_CACHED` and the same `model` variable.

### 3. `get_token_info` check outside routine cooldown gate
**Symptom:** MAGIC and HYPER tokens triggered a Claude call every 15 minutes regardless of cooldown.  
**Root cause:** The `get_token_info` scoring check ran before the `routine_ok` guard, so the 45-minute cooldown was bypassed.  
**Fix:** Moved the check inside the `routine_ok` block.  
**Invariant:** Any code path that calls Claude or performs expensive per-token work must be inside the `routine_ok` gate. Never add early-exit logic that fires Claude outside the cooldown.

### 4. Crash loop burned $20 in credits with no trades (July 1–2 2026)
**Symptom:** Bot spent ~$20 in ~11 hours without making any trades. Logs showed a Claude call every 30-60 seconds.  
**Root cause:** Two separate NameErrors caused the bot to crash on every tick. Each crash reset the in-memory 45-minute cooldown, so the next restart immediately triggered a Claude call. At ~$0.04/call and one crash per 30–60s, this burns ~$4.80/hour.  
**Bug 1 — `ROUTINE_COOLDOWN` out of scope:** The variable was a local in `_needs_claude_review()` but referenced in `run_once()` (different method). Python NameError on every crash.  
**Bug 2 — `send_alert` closure bug:** A `from bot.emailer import send_alert` inside a `try` block in `run()` shadowed the module-level import. Python treated `send_alert` as a local variable in `run()` scope. `_check_daily_api_cost()` is a closure inside `run()` — it captured `send_alert` as a free variable, but if `audit_failures` was empty, the `try` block never ran and `send_alert` was unbound. Python error: "cannot access free variable 'send_alert' where it is not associated with a value in enclosing scope."  
**Fix:** Inline `45 * 60` at the one out-of-scope reference. Remove the redundant local import in main.py.  
**Invariant:** Any local variable in one method is NOT visible in other methods of the same class. Do not reference method-local variables from other methods. Do not re-import at function scope what is already imported at module scope — it can shadow the module-level name and break closures.

### 5. Credit exhaustion caused immediate retry loop
**Symptom:** When Anthropic credits ran out, the bot crashed and restarted every 30s, logging the same error in a tight loop.  
**Root cause:** `"usage limits"` and `"credit balance"` errors in the API call weren't extended into the routine cooldown — they let the next tick call Claude immediately.  
**Fix:** Credit errors now push `_last_routine_review_ts` forward by 4 hours.  
**Invariant:** Any `APIStatusError` containing `"usage limits"` or `"credit balance"` must set a long cooldown, not just log and return.

---

## Category: Trade Execution / Swap Logic

### 5. STRONG_BEAR regime allowed auto-execution
**Symptom:** Bot entered AERO in June 2025 despite STRONG_BEAR regime. Score=55 via trend+RSI+ATR combination bypassed AI review.  
**Root cause:** `_auto_execute()` had no regime check. Any score ≥ 55 auto-bought regardless of market regime.  
**Fix:** Gate at top of `_auto_execute()`: return immediately if regime is STRONG_BEAR.  
**Invariant:** `_auto_execute()` must always check regime before executing. STRONG_BEAR = no auto-execution, ever.

### 6. Sell reverts from fee tier mismatch
**Symptom:** All W/PENDLE/ZORA sells reverted on-chain.  
**Root cause:** Executor used wrong Uniswap pool fee tier for these tokens.  
**Fix:** Fee tier now read from token metadata; fallback logic for unlisted tokens.  
**Invariant:** Always use the correct pool fee tier from token metadata. Never assume 0.3%.

### 7. `amount_in_wei` exceeded actual on-chain balance
**Symptom:** Sell transactions reverted because requested amount exceeded wallet balance.  
**Root cause:** Recorded position amount slightly exceeded on-chain amount (dust, rounding).  
**Fix:** Cap `amount_in_wei` at actual on-chain balance before submitting swap.  
**Invariant:** Before any sell, query on-chain balance and cap the swap amount to that value. Never trust recorded amounts alone.

### 8. Aerodrome WETH swap used native ETH path instead of ERC-20
**Symptom:** WETH swaps reverted on Aerodrome.  
**Root cause:** Router chose native ETH path; WETH must use the ERC-20 path.  
**Fix:** Always use ERC-20 path for WETH on Aerodrome.  
**Invariant:** WETH is always treated as ERC-20, never as native ETH, on all routers.

### 9. TP re-trigger loop stranded position
**Symptom:** Take-profit sold 50% but never raised the remaining TP, causing it to re-trigger every tick.  
**Root cause:** `raise_take_profit()` wasn't called after partial exit, or swap failed silently.  
**Fix:** `raise_take_profit()` called after every successful partial TP sell; TP multiplied 1.5x.  
**Invariant:** After any partial TP exit, always call `raise_take_profit()`. Never re-enter a sell loop without confirming TP was raised.

### 10. Gas exhaustion on 2-hop swaps
**Symptom:** 2-hop swaps (token → WETH → USDC) ran out of gas and failed.  
**Root cause:** Gas limit set for single-hop swaps was too low for 2-hop paths.  
**Fix:** Detect 2-hop paths and use higher gas limit.  
**Invariant:** Gas limit must account for path length. 2-hop = at least 2x single-hop estimate.

### 11. Minimum trade size check blocked exits
**Symptom:** Bot refused to sell tiny positions because they fell below the $20 minimum trade size.  
**Root cause:** Trade size guard applied to both buys and sells.  
**Fix:** Minimum trade size applies to buys only. Sells are never blocked by size.  
**Invariant:** Never apply buy-side guards (min size, confidence, position cap) to sell orders.

### 12. Balance check blocked full-position sells
**Symptom:** Selling 100% of a position failed balance check.  
**Root cause:** Balance guard compared requested amount to total minus a reserve; full sells tripped the reserve.  
**Fix:** Full-position sells bypass the reserve check.  
**Invariant:** A `close_position` sell of the full lot must never be blocked by internal balance guards.

### 13. Price impact check blocked sells (not just buys)
**Symptom:** Mechanical stop-losses and take-profits were refused due to price impact.  
**Root cause:** Price impact guard ran on all swaps including exits.  
**Fix:** Price impact and liquidity guards apply to buys only. Forced sells (SL, TP) bypass them.  
**Invariant:** Stop-loss and take-profit exits must never be blocked by liquidity or impact guards.

### 14. Timed-out receipts left ghost positions
**Symptom:** Bot sold a token successfully but the sell showed as "unknown" status and the position remained open.  
**Root cause:** Receipt lookup timed out. Bot never re-checked the tx, so the position was never closed.  
**Fix:** `_verify_unknown_sells()` runs on every startup; re-checks all "unknown" txs and closes positions if confirmed.  
**Invariant:** Any tx with status "unknown" must be re-verified on restart. Ghost positions from timeout must not persist.

---

## Category: Price / Portfolio Valuation

### 15. CoinGecko zero price zeroed out both price dicts simultaneously
**Symptom:** Entire portfolio showed $0 value when CoinGecko returned a zero or missing price.  
**Root cause:** A zero/null CoinGecko response overwrote both `snapshot["prices"]` and `context["prices"]` with zeros.  
**Fix:** Skip any price update where the value is zero or None. Keep the old price.  
**Invariant:** Never overwrite a working price with zero. Zero from CoinGecko = stale response, not a real price. Skip and keep existing.

### 16. Stablecoin prices zeroed during CoinGecko rate limits
**Symptom:** USDC/USDT/DAI showed $0 during rate limit periods.  
**Root cause:** Rate-limited response returned empty; stablecoin prices were overwritten with zero.  
**Fix:** Stablecoin prices hardcoded as $1.00 fallback when CoinGecko is unavailable.  
**Invariant:** USDC, USDT, and DAI always have a $1.00 fallback. Never let stablecoin prices be zero.

### 17. WETH balance showed $0 due to price sync bug
**Symptom:** WETH holding showed $0 in portfolio despite having WETH.  
**Root cause:** `context["prices"]` and `snapshot["prices"]` got out of sync; WETH price from context wasn't backfilled into snapshot where the balance calc reads from.  
**Fix:** After `_refresh_held_token_prices()`, sync all prices from snapshot back into context for tokens not already there.  
**Invariant:** The two price dicts must be synced every tick. See CLAUDE.md "Two Prices Dicts" section — these are always separate objects.

### 18. Dashboard portfolio total used cost_basis instead of current price
**Symptom:** Dashboard overstated portfolio value (showed purchase price, not current value).  
**Root cause:** Fallback for unknown prices used `cost_basis_usd` instead of excluding the position.  
**Fix:** If a position's current price is unknown, exclude it from portfolio total entirely. Never use cost basis.  
**Invariant:** Portfolio value = sum of (current_price × tokens) for positions with known prices only. cost_basis is what was paid, not what it's worth.

### 19. Dashboard token cache fallback used stale purchase-time prices
**Symptom:** Portfolio value inflated because old cache entries (from time of purchase) were used.  
**Root cause:** Token cache entries with `cached_at` older than 4h reflected the purchase price, not current market price.  
**Fix:** `_is_cache_fresh()` helper enforces 4h staleness limit in dashboard.  
**Invariant:** Cache entries older than 4h must not be used for portfolio valuation. Stale = unknown.

### 20. Dashboard WETH price showed $0 during CoinGecko rate limits
**Symptom:** WETH price showed $0 on dashboard during rate-limit periods.  
**Root cause:** Dashboard only queried CoinGecko; no fallback to the cached price.  
**Fix:** Dashboard falls back to token cache when live fetch fails.  
**Invariant:** Dashboard always has at least one price source per token. Live → cache fallback, never zero.

---

## Category: Risk Guards / Position Management

### 21. Regime variable used before assignment
**Symptom:** `NameError: name 'regime' is not defined` crash in `_build_market_prompt()`.  
**Root cause:** `can_open_trade()` was called before `regime` was calculated.  
**Fix:** Calculate regime at the top of `_build_market_prompt()` before any risk calls.  
**Invariant:** Regime must be computed before any call to `can_open_trade()` or `get_risk_summary()`. See CLAUDE.md "Known Gotchas" #4.

### 22. Win rate guard blocked indefinitely after bad streak
**Symptom:** Win rate guard triggered and never cleared because lookback was 10 trades — bot needed 10 wins to recover.  
**Root cause:** WIN_RATE_LOOKBACK=10 was too long; a streak of losses locked the bot for too many trades.  
**Fix:** Reduced to 5 trades.  
**Invariant:** Win rate lookback should be small enough that normal recovery (~2-3 wins) clears the guard.

### 23. Position cap was too low, blocking new entries
**Symptom:** Bot refused to open trades with plenty of USDC available.  
**Root cause:** Hard-coded position cap of 4-7 didn't scale with portfolio size.  
**Fix:** Cap scales with portfolio value: 12 (<$500), 15 (<$2k), 20 (<$10k), 30 (larger). Regime applies only a light trim.  
**Invariant:** Available USDC is the real limit on new trades. Position slot count should rarely be the binding constraint.

### 24. Ghost positions from missing on-chain balance
**Symptom:** Bot managed positions it no longer held (token sold elsewhere or failed tx).  
**Root cause:** No startup check against actual on-chain balance.  
**Fix:** `_reconcile_positions()` runs on every startup; removes positions where on-chain balance < 1% of recorded.  
**Invariant:** On startup, always reconcile recorded positions against on-chain balances.

### 25. Downtime extension applied multiple times on crash-restart loops
**Symptom:** `max_hold_until` extended by cumulative downtime on repeated restarts.  
**Root cause:** `_record_tick()` wasn't called until after `run_once()` succeeded; crash before that re-applied the same extension.  
**Fix:** `_record_tick()` called immediately after applying the hold extension (inside `_adjust_positions_for_downtime()`).  
**Invariant:** Record the tick timestamp before the extension can be re-applied. See CLAUDE.md "Known Gotchas" #6.

---

## Category: Data Integrity

### 26. `realized_gains.csv` missing `entry_reasoning` field
**Symptom:** KeyError crash when reading realized gains; field missing from CSV header.  
**Root cause:** `entry_reasoning` column added to the data model but not to the CSV writer fieldnames.  
**Fix:** Added field to fieldnames list in the CSV writer.  
**Invariant:** Any new field added to position or trade data must also be added to all CSV writers and readers that touch that data.

### 27. Screener cache overwritten with empty results
**Symptom:** Watchlist disappeared after a screener API failure.  
**Root cause:** Failed screener call returned empty list; cache was overwritten with [].  
**Fix:** Never overwrite `screener_cache.json` with an empty result.  
**Invariant:** Cache writes are only allowed when the new data is non-empty. Failures preserve the last good data.

### 28. Dusting attack: bot tried to sell airdropped tokens
**Symptom:** Bot attempted to swap a random airdropped token it didn't buy, potentially calling a draining contract.  
**Root cause:** No check that the sell target was in `open_positions`.  
**Fix:** Before any sell, verify the token is in `open_positions`. Reject all others.  
**Invariant:** Bot must never sell a token it didn't explicitly buy. Only tokens in open_positions are sellable.

---

## Category: Code Patterns That Cause Bugs

These are recurring patterns responsible for multiple bugs above. Check for them in every code review:

1. **Price zero propagation** — Never assign a zero or None price from any external API. Guard every price update: `if price and price > 0`.

2. **Sell-side guards applied to exits** — Any guard that should apply to buys must be explicitly excluded from sells. SL/TP exits must always go through.

3. **Two prices dicts out of sync** — `snapshot["prices"]` and `context["prices"]` are separate objects. Any change to one must be reflected in the other if both are used downstream.

4. **Claude called outside cooldown gate** — Any per-token evaluation that touches Claude must be inside `routine_ok`. Never put Claude calls in early-return paths that bypass the cooldown.

5. **Hardcoded model in follow-up calls** — Any agentic loop follow-up `messages.create()` must use the `model` variable, not a hardcoded string.

6. **Missing `cache_control` on API calls** — Every `messages.create()` call must pass `system=SYSTEM_PROMPT_CACHED`. Adding a new API call site? Add `SYSTEM_PROMPT_CACHED`, not `SYSTEM_PROMPT`.

7. **CSV field mismatch** — Adding a field to a data class or dict means adding it to every CSV writer/reader for that data. Check all write sites in the table in CLAUDE.md.

8. **Guard ordering** — Regime must be calculated before any risk call. Order in `_build_market_prompt()` is non-negotiable.

9. **Method-local variables referenced from other methods** — A local variable defined in one method (e.g. `ROUTINE_COOLDOWN` in `_needs_claude_review`) is not visible in any other method. If a value is needed in multiple methods, define it as a class constant or module constant.

10. **Duplicate imports shadowing module-level names / breaking closures** — If a name is already imported at module level, do NOT re-import it inside a function. The local `from x import y` makes Python treat `y` as a local variable in that function's scope. Any nested closure that references `y` will fail if the local assignment is only in a conditional branch that wasn't reached.
