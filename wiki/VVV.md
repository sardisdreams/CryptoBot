---
symbol: VVV
name: Venice Token
chain: Base
contract: "0xacfE6019Ed1A7Dc6f7B508C02d1b04ec88cC21bf"
cg_id: venice-token
decimals: 18
type: DeFi / AI Protocol Token
risk: Medium
status: ACTIVE — designated swing trade target
---

# Venice Token (VVV)

## What it is
VVV is the governance and utility token for Venice, a privacy-focused AI platform built on Base. Venice allows users to interact with AI models without data being stored or shared.

## Fundamentals
- Privacy-first AI platform built natively on Base chain
- ~$900M market cap (as of June 2026), ranked ~#78 on CoinGecko
- $80–90M+ daily trading volume — real liquidity
- Emissions reduction started June 1, 2026 — bullish supply catalyst

## Trading notes
- **Active swing trade target** — configured in bot/config.py SWING_TARGETS
- Strategy: buy near weekly low, sell at +8%, sit in USDC, repeat
- Parameters: TP=8%, SL=8%, max_hold=18h
- Weekly range auto-updates from price history each cycle
- Buy when RSI is oversold OR price is near the bottom of the recent range
- Do NOT hold for 20%+ gains — the edge is fast turnover

## Key catalysts to watch
- Emissions reduction ongoing — reduces sell pressure
- AI narrative strength (correlates with broader AI token sector)
- Base chain ecosystem growth

## Risk factors
- High volatility — can drop 15–20% in a day
- Narrative-driven: AI hype cycles can reverse quickly
- Monitor Aerodrome pool depth before entry
