# Trading Strategy

## Primary Goal
Grow total USD portfolio value aggressively but safely. Target opportunities with 10%+ return potential. BTC and ETH alone are insufficient for this goal — focus on emerging Base-native tokens with real projects and growing adoption.

## Asset Focus

### Primary targets — Base-native low/mid cap tokens
- Market cap range: $10M–$500M (sweet spot for meaningful upside)
- Must be Base-native or have strong Base presence
- Must pass full vetting checklist before being added to the whitelist
- Position size: 5–15% per coin (never over-concentrate)

### Secondary — Large caps (ETH, cbBTC)
- Used as a "safe harbor" during uncertainty or while waiting for opportunities
- Not the primary growth driver — held when nothing better is available
- Max 20% of portfolio in ETH/cbBTC combined unless no better opportunities exist

### Reserve — USDC
- Always maintain at least 30% in USDC (reduced from 50% to allow more active deployment)
- Idle USDC is lost opportunity — deploy it when good setups appear
- Always keep 0.005 ETH minimum for gas

## Entry Setups

One strong signal is enough to act. RSI is informative context, not a hard gate. The agent reads all available signals and uses judgment. These are the five core setups to look for:

### Setup 1 — Momentum Continuation
A token is moving with conviction: positive 1h momentum (>2%) with above-average volume. RSI is not yet overbought (<70). The move has room to continue.
- **Action:** Buy 5–15% of portfolio
- **Watch:** Volume must be real — check it's not a single large trade

### Setup 2 — Dip Recovery
A token is down 5–15% over 24h but the 1h is turning positive. The sell-off is exhausting and buyers are stepping in. RSI below 50 gives room to run.
- **Action:** Buy 10% of portfolio on the turn
- **Watch:** If 1h momentum stalls again, exit — don't catch a falling knife

### Setup 3 — Catalyst Trade
A known upcoming event is in notes.txt (emission change, product launch, listing, partnership). Token has real liquidity (>$200K pool). Enter before the event, exit into the reaction.
- **Action:** Buy 10–15% ahead of catalyst
- **Watch:** Sell into strength after the event regardless of how it feels — catalysts often sell the news

### Setup 4 — TVL Rising (DeFi Adoption Signal)
DeFiLlama shows a Base protocol's TVL up >5% in 24h with the token showing positive price momentum. Real capital flowing in is a genuine adoption signal.
- **Action:** Buy 5–10% of portfolio
- **Watch:** Make sure TVL rise isn't a single whale deposit — check if it's sustained

### Setup 5 — RSI Oversold + Momentum Turn
RSI below 30 AND 1h momentum has just turned positive after being negative. The oversold condition is resolving. High-conviction setup on solid projects only.
- **Action:** Buy up to 15% of portfolio
- **Watch:** Only use this on established tokens with real liquidity — oversold on a thin low-cap can mean it's dying

## Exit Rules
No fixed rules — the agent reads conditions and decides. General guidance:
- Sell into strength, not weakness
- When a position is up significantly and momentum is fading, start reducing
- If a trade thesis breaks (catalyst failed, TVL drops back), exit regardless of P&L
- Always sell back to USDC

## What makes a good low-cap opportunity
- Real project: working product, active development, legitimate team
- Growing on-chain activity: TVL rising, transaction volume increasing
- Upcoming catalyst: emission changes, product launch, partnership, listing
- Reasonable liquidity: >$200K on Aerodrome/Uniswap (can enter/exit without huge slippage)
- Community: active Twitter, Discord, real engagement (not bots)
- Age: ideally 3+ months live on mainnet with no major incidents

## Token Vetting — Required before any new token trades
Every new token must pass this checklist before being added to `bot/config.py`:

- [ ] Contract verified on Basescan (no unverified bytecode)
- [ ] No mint function that allows infinite supply
- [ ] No owner-only transfer restrictions (honeypot check)
- [ ] Liquidity locked or owned by protocol (not a single wallet)
- [ ] Top 10 holders own <50% of supply (not a whale trap)
- [ ] Project website active and professional
- [ ] Twitter/X account with real engagement (check for bot followers)
- [ ] Has been live on mainnet for at least 3 months
- [ ] Aerodrome/Uniswap liquidity >$200K
- [ ] No recent large wallet dumps visible on Basescan

## Position sizing
- No rigid rules — size based on conviction and risk score
- Low risk score: max 5% of portfolio
- Medium risk score: max 10% of portfolio
- High risk score: max 15% of portfolio (only with strong catalyst)
- Never put more than 15% in a single low-cap position

## Exit rules
- Always sell back to USDC (not into another coin)
- Take partial profits at +20%, +40%, +60% milestones
- Hard stop: if a position drops 25% from entry, sell and reassess
- If a catalyst fails to materialize, exit — don't hold hoping for recovery

## Pump and dump protection
- Sudden >20% price spike with no news = likely pump, avoid or exit
- Trending on CoinGecko without fundamentals = warning sign
- Very low market cap (<$5M) = too easy to manipulate, skip
- New token (<1 month old) = skip regardless of hype

## Measure of success
Total portfolio value in USD over 30-day rolling periods.
