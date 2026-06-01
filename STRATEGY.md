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
