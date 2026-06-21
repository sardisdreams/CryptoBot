"""
Dynamic performance tiers — adjusts API spending based on realized + unrealized P&L.
More profit = tighter scan intervals, lower Sonnet escalation threshold.
Loss / breakeven = Haiku only, conservative signals.
"""
from bot.positions import get_realized_summary, get_position_summary
from bot.logger import setup_logger

logger = setup_logger("performance")

# Tier definitions: (min_profit_usd, label, interval_seconds, sonnet_threshold_pct, always_sonnet)
TIERS = [
    (-999999, "CONSERVE",  3600, 5.0, False),  # loss or flat   → 60min, Haiku preferred
    (2,       "CAUTIOUS",  1800, 4.0, False),  # +$2            → 30min
    (50,      "ACTIVE",     900, 3.0, False),  # +$50           → 15min
    (100,     "AGGRESSIVE", 600, 2.0, False),  # +$100          → 10min
    (200,     "FULL",       300, 1.5, True),   # +$200          → 5min, always Sonnet
]


def get_tier(prices: dict) -> dict:
    """
    Calculate current performance tier based on total P&L.
    Returns a dict with tier name, interval, sonnet_threshold, always_sonnet.
    """
    try:
        realized   = get_realized_summary()
        open_pos   = get_position_summary(prices)
        unrealized = sum(p["gain_loss_usd"] for p in open_pos)
        total_pnl  = realized["total_realized_gain_usd"] + unrealized

        # Find highest tier we qualify for
        selected = TIERS[0]
        for tier in TIERS:
            if total_pnl >= tier[0]:
                selected = tier

        min_pnl, label, interval, threshold, always_sonnet = selected

        logger.info(
            f"Performance tier: {label} | "
            f"P&L: ${total_pnl:+.2f} | "
            f"Interval: {interval//60}min | "
            f"Sonnet threshold: {threshold}%"
        )

        return {
            "label":            label,
            "interval_seconds": interval,
            "sonnet_threshold": threshold,
            "always_sonnet":    always_sonnet,
            "total_pnl":        round(total_pnl, 2),
        }

    except Exception as e:
        logger.warning(f"Performance tier calculation failed: {e} — defaulting to CONSERVE")
        return {
            "label":            "CONSERVE",
            "interval_seconds": 3600,
            "sonnet_threshold": 5.0,
            "always_sonnet":    False,
            "total_pnl":        0.0,
        }
