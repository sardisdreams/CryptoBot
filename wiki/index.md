# Coin Wiki Index

This directory stores research, historical data, and trading notes for each asset.
The agent reads these files every tick to inform its decisions.

## Whitelisted (tradeable)
| Symbol | Name | Risk | File |
|---|---|---|---|
| WETH | Wrapped Ether | Low | [WETH.md](WETH.md) |
| cbBTC | Coinbase Wrapped Bitcoin | Low-Medium | [cbBTC.md](cbBTC.md) |
| cbETH | Coinbase Wrapped Staked ETH | Low-Medium | [cbETH.md](cbETH.md) |
| USDC | USD Coin | Very Low | [USDC.md](USDC.md) |

## Watchlist (not yet tradeable — under review)
| Symbol | Name | Risk | File |
|---|---|---|---|
| VVV | Venice Token | High | [VVV.md](VVV.md) |

## How to add a new coin
1. Create `wiki/SYMBOL.md` using the template below
2. Complete the vetting checklist in the file
3. Add the contract address + decimals to `bot/config.py` TOKENS
4. Move the coin from Watchlist to Whitelisted in this index

## Template
Copy this to create a new wiki entry:
```
---
symbol: SYMBOL
name: Full Name
chain: Base
contract: "0x..."
decimals: 18
type: (DeFi / L1 / Stablecoin / etc)
risk: (Very Low / Low / Medium / High)
status: WATCHLIST or ACTIVE
---
# Full Name (SYMBOL)
## What it is
## Links
## Fundamentals
## Key catalysts to watch
## Trading notes
## Risk factors
```
