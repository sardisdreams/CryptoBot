# Trading Strategy

## Goal
Grow the total USD value of the portfolio in an aggressive but safe way.

## Guidelines (not hard rules — agent uses judgment)

- **USDC is home base.** Profits should always be cycled back to USDC. When a trade closes or a position reaches its target, sell back to USDC.
- **Aggressive but safe.** Take meaningful positions when there is conviction. Don't be timid, but don't blow up the portfolio on a single trade.
- **Micro trades are fine.** Small opportunistic trades are encouraged — not every move needs to be large.
- **Always keep a USDC reserve.** At least 50% of the portfolio should remain in USDC at all times as a stability floor.
- **Always keep gas.** Never drop below 0.005 ETH — needed to execute any transaction.
- **Preserve capital first.** When the market is uncertain, stay in USDC. Idle USDC is not a loss.
- **No borrowing.** Spot trading only.

## Tradeable Assets
Only tokens explicitly listed in `bot/config.py` TOKENS registry may be traded.
Currently approved: WETH, cbBTC, cbETH, USDC, USDT, DAI.

**Before adding any new token to the registry, it must pass all of the following checks manually:**
- Has an established, active website
- Has a verifiable Twitter/X account with real engagement
- Has existed for at least 6 months with a track record
- Has significant liquidity and trading volume (not a thin market)
- Is not a recently launched token or meme coin
- Has a clear, legitimate project behind it

## Token Safety Rules
- **Never trade tokens not in the whitelist** — no exceptions
- Be skeptical of any token showing sudden large price movements with no news
- Pump-and-dump patterns: sudden volume spike + price surge on a low-cap token = avoid
- If a token's price moves more than 20% in a single tick with no obvious catalyst, treat it as suspicious and hold USDC instead

## Measure of Success
Total portfolio value in USD, measured over time.
