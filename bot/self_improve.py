"""
Self-improvement engine — runs each cycle and analyses the bot's own performance.
Writes actionable observations to the knowledge base so Claude reads them next tick.
The bot continuously refines its strategy based on what is and isn't working.
"""
import csv
import json
import os
from datetime import datetime, timezone
from bot import knowledge
from bot.logger import setup_logger

logger = setup_logger("self_improve")

REALIZED_FILE = "records/realized_gains.csv"
ANALYSIS_FILE = "data/self_analysis.json"


def _load_trades() -> list[dict]:
    if not os.path.exists(REALIZED_FILE):
        return []
    with open(REALIZED_FILE, newline="") as f:
        return list(csv.DictReader(f))


def _load_analysis() -> dict:
    if not os.path.exists(ANALYSIS_FILE):
        return {"last_trade_count": 0, "last_run": ""}
    with open(ANALYSIS_FILE) as f:
        return json.load(f)


def _save_analysis(data: dict):
    os.makedirs("data", exist_ok=True)
    with open(ANALYSIS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def run_self_analysis():
    """
    Analyse recent trading performance and write insights to the knowledge base.
    Only runs when new trades have closed since last analysis.
    """
    trades = _load_trades()
    analysis = _load_analysis()

    # Only analyse when we have new closed trades
    if len(trades) == analysis.get("last_trade_count", 0):
        return
    if len(trades) < 3:
        return

    logger.info(f"Running self-analysis on {len(trades)} closed trades...")

    recent = trades[-10:]  # focus on last 10 trades

    wins   = [t for t in recent if float(t.get("gain_loss_pct", 0)) >= 0]
    losses = [t for t in recent if float(t.get("gain_loss_pct", 0)) < 0]
    win_rate = len(wins) / len(recent) * 100 if recent else 0

    insights = []

    # Win rate trend
    if len(trades) >= 10:
        early_wr = sum(1 for t in trades[-10:-5] if float(t.get("gain_loss_pct", 0)) >= 0) / 5 * 100
        recent_wr = sum(1 for t in trades[-5:] if float(t.get("gain_loss_pct", 0)) >= 0) / 5 * 100
        if recent_wr > early_wr + 20:
            insights.append(f"Win rate improving: last 5 trades {recent_wr:.0f}% vs prior 5 {early_wr:.0f}%. Strategy is working — maintain current approach.")
        elif recent_wr < early_wr - 20:
            insights.append(f"Win rate declining: last 5 trades {recent_wr:.0f}% vs prior 5 {early_wr:.0f}%. Review recent entry signals — consider being more selective.")

    # Hold time analysis
    if wins:
        avg_win_hold  = sum(int(t.get("hold_days", 0)) for t in wins) / len(wins)
        avg_loss_hold = sum(int(t.get("hold_days", 0)) for t in losses) / len(losses) if losses else 0
        if avg_loss_hold > avg_win_hold * 1.5:
            insights.append(f"Losses held longer than wins ({avg_loss_hold:.1f}d vs {avg_win_hold:.1f}d). Cut losses faster — exit when thesis breaks, don't wait.")

    # Token performance patterns
    token_stats = {}
    for t in recent:
        tok = t.get("token", "?")
        pnl = float(t.get("gain_loss_pct", 0))
        if tok not in token_stats:
            token_stats[tok] = []
        token_stats[tok].append(pnl)

    for tok, pnls in token_stats.items():
        avg = sum(pnls) / len(pnls)
        if len(pnls) >= 2 and avg < -5:
            insights.append(f"{tok} has averaged {avg:+.1f}% across {len(pnls)} trades. Reduce position size or avoid until pattern improves.")
        elif len(pnls) >= 2 and avg > 5:
            insights.append(f"{tok} has averaged {avg:+.1f}% across {len(pnls)} trades. Strong performer — prioritise when setup appears.")

    # Average win vs loss size
    if wins and losses:
        avg_win_usd  = sum(float(t.get("gain_loss_usd", 0)) for t in wins) / len(wins)
        avg_loss_usd = abs(sum(float(t.get("gain_loss_usd", 0)) for t in losses) / len(losses))
        rr = avg_win_usd / avg_loss_usd if avg_loss_usd > 0 else 0
        if rr < 1.0:
            insights.append(f"Risk-reward ratio is {rr:.2f} (wins ${avg_win_usd:.2f} vs losses ${avg_loss_usd:.2f}). Need larger wins or smaller losses. Consider wider TP or tighter SL.")
        elif rr > 1.5:
            insights.append(f"Strong risk-reward: {rr:.2f}x (wins ${avg_win_usd:.2f} vs losses ${avg_loss_usd:.2f}). Current TP/SL structure is working well.")

    # Overall summary
    summary = (
        f"Self-analysis ({len(recent)} recent trades): "
        f"win rate {win_rate:.0f}% | "
        f"avg win {sum(float(t.get('gain_loss_pct',0)) for t in wins)/len(wins):.1f}% | "
        f"avg loss {sum(float(t.get('gain_loss_pct',0)) for t in losses)/len(losses):.1f}%"
        if wins and losses else f"Self-analysis: win rate {win_rate:.0f}%"
    )

    # Write all insights to knowledge base
    for insight in insights:
        knowledge.add_entry("strategy", f"[AUTO-ANALYSIS] {insight}")
        logger.info(f"Self-improvement insight: {insight}")

    if insights:
        knowledge.add_entry("strategy", f"[PERFORMANCE SUMMARY] {summary}")

    # Save state so we don't re-analyse the same trades
    _save_analysis({
        "last_trade_count": len(trades),
        "last_run": datetime.now(timezone.utc).isoformat(),
        "last_win_rate": round(win_rate, 1),
        "insights_generated": len(insights),
    })

    logger.info(f"Self-analysis complete: {len(insights)} insights written to knowledge base")
