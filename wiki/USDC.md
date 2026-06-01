---
symbol: USDC
name: USD Coin
chain: Base
contract: "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
decimals: 6
type: Stablecoin (fiat-backed)
risk: Very Low
---

# USD Coin (USDC)

## What it is
USDC is a US dollar stablecoin issued by Circle, backed 1:1 by cash and short-term US treasuries. It is the primary stable reserve currency for this bot and the most liquid stablecoin on Base.

## Links
- Website: https://www.circle.com/usdc
- Twitter: https://twitter.com/circle
- Basescan: https://basescan.org/token/0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913

## Fundamentals
- Issued by Circle (US-regulated, audited monthly)
- Backed 1:1: cash + US Treasuries held in regulated US banks
- Native USDC on Base (not bridged) — most liquid stablecoin on the chain
- Circle can freeze addresses (blacklist function exists in the contract)
- Market cap: ~$45B+ (second largest stablecoin after USDT)

## Role in this bot
- **Primary reserve currency** — the bot targets 50%+ allocation in USDC at all times
- All profits are cycled back to USDC after trades
- Used as the "safe" position during uncertain market conditions
- Gas is paid in ETH, not USDC

## Risk factors
- De-peg risk: briefly de-pegged to $0.87 during Silicon Valley Bank collapse (Mar 2023) — recovered within days
- Regulatory risk: Circle is US-regulated and subject to government orders
- Circle insolvency risk (very low — regular audits, conservative reserves)
- Not truly decentralized — address blacklisting is possible

## Historical de-peg events
| Date | Event | Low |
|---|---|---|
| Mar 2023 | SVB bank collapse (Circle held $3.3B there) | $0.87 |
| All other periods | Stable | ~$1.00 |
