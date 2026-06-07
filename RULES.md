# CryptoBot — Invariants & Rules

This file is the pre-flight checklist. Before shipping any change, verify every
rule in the relevant section still holds. If a change would violate a rule,
fix the rule violation first or explicitly update this file with the new contract.

---

## 1. Price Pipeline

| # | Rule | Violated by |
|---|------|-------------|
| P1 | Strip zero prices from `market.get_all_prices()` before fallback chains run | Rate-limited call returning 0 blocks all fallbacks |
| P2 | Cache fallback applies when `price == 0`, not just when key is missing | `if sym not in prices` misses zero-priced keys |
| P3 | Never use `cost_basis_usd` as a price proxy | That's what was paid, not current value — overstates portfolio |
| P4 | Dashboard cache fallback rejects entries older than 4h | Stale purchase-time prices inflate total (e.g. PENDLE $8.24 vs $1.44) |
| P5 | Custom token prices inject into `snapshot["prices"]`, not `context["prices"]` | `_execute_tool` reads snapshot; injecting into context means bot can't sell |
| P6 | After `_refresh_held_token_prices(snapshot["prices"])`, sync into `context["prices"]` | LLM prompt would show stale prices for custom tokens |

---

## 2. Balance & Portfolio Total

| # | Rule | Violated by |
|---|------|-------------|
| B1 | `_get_full_balances` must query ALL held tokens: ETH (native), all TOKENS registry entries (including WETH), and all custom tokens with known addresses | Skipping WETH caused $54 discrepancy vs wallet app |
| B2 | Dashboard `total_usd` comes from `balances["_total_usd"]` (on-chain) not `positions.json × prices` | Position-record arithmetic diverges from wallet whenever a price is 0 or stale |
| B3 | Never include a position in the total if its price is unknown — exclude it | Unknown price → $0 contribution is correct; cost_basis fallback overstates |

---

## 3. Data Integrity (positions.json)

| # | Rule | Violated by |
|---|------|-------------|
| D1 | Every write to `positions.json` must use atomic write-then-rename (`_save()` in positions.py, or `.tmp` + `os.replace()` in main.py) | Bare `open+write` can corrupt file on crash mid-write |
| D2 | Every position lot for a custom token must store `cg_id` | Without cg_id, `_refresh_held_token_prices` can't price it; bot can never exit cleanly |
| D3 | `cg_id` chain: `execute_swap(token_out_cg_id=...)` → `open_position(cg_id=...)` → lot in positions.json → `_refresh_held_token_prices` reads it | Break anywhere in this chain = permanently unpriced position |

---

## 4. API Cost Controls

| # | Rule | Violated by |
|---|------|-------------|
| C1 | Budget guard: if monthly Anthropic spend ≥ 80% of `ANTHROPIC_BUDGET_USD`, force Haiku regardless of market signals | Positions stuck down >15% burn Sonnet every tick in bear markets |
| C2 | BEAR or STRONG_BEAR regime → Haiku unless a position is ≥ 30% P&L (either direction) | Old 15% threshold + underwater positions = Sonnet every hour |
| C3 | Neutral/bull regime → Sonnet only if position ≥ 25% P&L, extreme F&G, or significant 1h move | Lower thresholds burn budget on normal volatility |
| C4 | Performance tier controls tick interval — CONSERVE (60min) when P&L is negative | Fast ticks × Sonnet = budget exhausted mid-month |

---

## 5. Risk Guards (must never be bypassed by code changes)

| # | Rule | Enforced in |
|---|------|-------------|
| R1 | Daily drawdown > 10% → halt new trades | `risk.py` |
| R2 | Win rate < 40% over last 10 trades → halt new trades | `risk.py` |
| R3 | Stop-out cooldown: 30min no re-entry after SL hit on a token | `risk.py` |
| R4 | Capital floor never shrinks — 10% of every realized profit locked permanently | `capital.py` |
| R5 | Recovery mode: USDC < capital floor → no new trades | `capital.py` |
| R6 | Dusting attack guard: token must appear in `open_positions` before any sell executes | `executor.py` |
| R7 | Position cap scales with portfolio size and regime — agent cannot override | `risk.py` |

---

## 6. Mechanical Exits (run before AI agent, no AI reasoning)

| # | Rule |
|---|------|
| M1 | Mechanical exits (`positions.check_mechanical_exits()`) always run BEFORE the AI agent tick |
| M2 | Stop loss is forced — sell 100% immediately, no agent override |
| M3 | Take profit sells exactly 50% of lot, then raises remaining lot's TP by 1.5× (prevents re-trigger) |
| M4 | Hold window expiry is a soft suggestion to the agent, not a forced exit |

---

## 7. Downtime Handling

| # | Rule |
|---|------|
| T1 | `_record_tick()` must be called INSIDE `_adjust_positions_for_downtime()` immediately after applying extensions — before `run_once()` — to prevent crash-loop re-application |
| T2 | Gap < 2h is ignored (normal restarts); gap is capped at 7 days |
| T3 | `_record_tick()` is also called after every successful `run_once()` |

---

## 8. Deployment

| # | Rule |
|---|------|
| X1 | Every response that includes a `git push` must also include the deploy command: `ssh root@143.198.37.28 'bash /opt/cryptobot/app/deploy/update.sh'` |
| X2 | Never leave changes uncommitted at end of session |
| X3 | Deploy script does `git pull` from GitHub — changes must be on GitHub before deploying |

---

## Pre-flight Checklist (run mentally before every PR)

- [ ] Does this change touch prices? → Check P1–P6
- [ ] Does this change touch balances or portfolio total? → Check B1–B3
- [ ] Does this change write to positions.json? → Check D1–D3
- [ ] Does this change affect model selection or tick frequency? → Check C1–C4
- [ ] Does this change touch executor, risk, or capital? → Check R1–R7
- [ ] Does this change touch exit logic? → Check M1–M4
- [ ] Am I about to push? → Check X1–X3
