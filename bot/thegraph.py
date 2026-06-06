"""
TheGraph integration — queries Uniswap V3 on Base for pool-level liquidity data.
Free tier: 100k queries/month. No API key required for public subgraphs.

Provides: in-range liquidity, total pool TVL, recent swap volume.
This is more accurate than CoinGecko for assessing actual trading liquidity.
"""
import requests
import certifi
from bot.logger import setup_logger

logger = setup_logger("thegraph")

SSL = certifi.where()

# Uniswap V3 Base subgraph
UNISWAP_V3_BASE = "https://api.thegraph.com/subgraphs/name/messari/uniswap-v3-base"
USDC_ADDRESS    = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"

_POOL_CACHE: dict = {}


def get_pool_data(token_address: str, timeout: int = 8) -> dict | None:
    """
    Query Uniswap V3 subgraph for pool data for a given token vs USDC.
    Returns TVL, volume, and liquidity depth info.
    Cached per session — pool data doesn't change rapidly.
    """
    key = token_address.lower()
    if key in _POOL_CACHE:
        return _POOL_CACHE[key]

    query = """
    {
      pools(
        where: {
          token0_in: ["%s", "%s"],
          token1_in: ["%s", "%s"]
        }
        orderBy: totalValueLockedUSD
        orderDirection: desc
        first: 3
      ) {
        id
        token0 { symbol }
        token1 { symbol }
        feeTier
        totalValueLockedUSD
        volumeUSD
        txCount
        token0Price
        token1Price
        liquidity
      }
    }
    """ % (key, USDC_ADDRESS, key, USDC_ADDRESS)

    try:
        resp = requests.post(
            UNISWAP_V3_BASE,
            json={"query": query},
            timeout=timeout,
            verify=SSL,
        )
        resp.raise_for_status()
        pools = resp.json().get("data", {}).get("pools", [])

        if not pools:
            result = {"found": False, "tvl_usd": 0, "volume_24h": 0}
        else:
            # Pick the most liquid pool
            best = max(pools, key=lambda p: float(p.get("totalValueLockedUSD", 0) or 0))
            tvl = float(best.get("totalValueLockedUSD", 0) or 0)
            vol = float(best.get("volumeUSD", 0) or 0)
            result = {
                "found":      True,
                "pool_id":    best.get("id", ""),
                "fee_tier":   int(best.get("feeTier", 3000)),
                "tvl_usd":    round(tvl, 2),
                "volume_usd": round(vol, 2),
                "tx_count":   int(best.get("txCount", 0) or 0),
                "liquidity":  best.get("liquidity", "0"),
                "deep_enough": tvl >= 100_000,  # $100k TVL minimum for safe trading
            }
            logger.info(
                f"TheGraph pool: {best['token0']['symbol']}/{best['token1']['symbol']} "
                f"| TVL ${tvl:,.0f} | vol ${vol:,.0f} | fee {int(best.get('feeTier',3000))/10000:.2f}%"
            )

        _POOL_CACHE[key] = result
        return result

    except Exception as e:
        logger.warning(f"TheGraph query failed for {token_address}: {e}")
        return None


def check_pool_liquidity(token_address: str, trade_size_usd: float) -> dict:
    """
    Check if a token's Uniswap V3 pool has sufficient liquidity for the trade size.
    Returns {ok: bool, reason: str, tvl: float}.
    """
    data = get_pool_data(token_address)
    if data is None:
        return {"ok": True, "reason": "TheGraph unavailable — allowing (use on-chain quote as fallback)", "tvl": 0}

    if not data.get("found"):
        return {"ok": False, "reason": "No Uniswap V3 pool found on Base for this token", "tvl": 0}

    tvl = data.get("tvl_usd", 0)
    # Trade size should be < 1% of pool TVL to avoid significant price impact
    if tvl > 0 and trade_size_usd / tvl > 0.01:
        impact_est = round(trade_size_usd / tvl * 100, 2)
        return {
            "ok":     False,
            "reason": f"Trade ${trade_size_usd:.0f} is {impact_est}% of pool TVL ${tvl:,.0f} — too large relative to pool size",
            "tvl":    tvl,
        }

    if not data.get("deep_enough"):
        return {
            "ok":     False,
            "reason": f"Pool TVL too low: ${tvl:,.0f} (need $100k+). High price impact likely.",
            "tvl":    tvl,
        }

    return {"ok": True, "reason": f"Pool TVL ${tvl:,.0f} — sufficient for ${trade_size_usd:.0f} trade", "tvl": tvl}
