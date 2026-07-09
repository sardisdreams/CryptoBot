"""
Simple backtesting engine using stored price history.
Simulates the bot's signal logic on historical data to evaluate strategy performance.

Usage:
  python -m bot.backtest --symbol VVV --days 30
  python -m bot.backtest --symbol WETH --candle 4h
"""
import json
import os
import argparse


def _load_history(symbol: str) -> list[dict]:
    path = "data/price_history.json"
    if not os.path.exists(path):
        return []
    with open(path) as f:
        data = json.load(f)
    return data.get(symbol, [])


def _calc_rsi(prices: list[float], period: int = 14) -> float | None:
    if len(prices) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(prices)):
        d = prices[i] - prices[i-1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    if al == 0:
        return 100.0
    return round(100 - 100 / (1 + ag / al), 2)


def _calc_sma(prices: list[float], period: int) -> float | None:
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period


def run_backtest(
    symbol: str,
    take_profit_pct: float = 8.0,
    stop_loss_pct: float = 8.0,
    min_rsi_buy: float = None,    # only buy if RSI below this (None = ignore)
    max_rsi_buy: float = 50.0,    # only buy if RSI below this
    momentum_threshold: float = 0.0,  # min 1h momentum % to trigger buy
    trade_size_usd: float = 30.0,
    verbose: bool = False,
) -> dict:
    """
    Simulate strategy on stored price history for a given symbol.
    Returns performance statistics.
    """
    entries = _load_history(symbol)
    if len(entries) < 20:
        return {"error": f"Insufficient price history for {symbol} ({len(entries)} points, need 20+)"}

    prices = [e["price"] for e in entries if e.get("price", 0) > 0]
    timestamps = [e.get("ts", "") for e in entries if e.get("price", 0) > 0]

    trades = []
    in_position = False
    entry_price = 0.0
    entry_idx = 0

    for i in range(20, len(prices)):
        window = prices[:i]
        current = prices[i]
        rsi = _calc_rsi(window)
        mom1h = (prices[i] - prices[i-2]) / prices[i-2] * 100 if i >= 2 and prices[i-2] > 0 else 0

        if not in_position:
            # Entry conditions
            rsi_ok = (rsi is not None) and (rsi < max_rsi_buy)
            if min_rsi_buy:
                rsi_ok = rsi_ok and rsi > min_rsi_buy
            mom_ok = mom1h >= momentum_threshold

            if rsi_ok and mom_ok:
                in_position = True
                entry_price = current
                entry_idx = i
                if verbose:
                    print(f"  BUY  @ ${current:.4f} | RSI={rsi:.1f} | mom={mom1h:+.2f}% | {timestamps[i][:16]}")

        else:
            # Exit conditions
            pnl_pct = (current - entry_price) / entry_price * 100
            held = i - entry_idx

            if pnl_pct >= take_profit_pct:
                trades.append({"pnl_pct": pnl_pct, "held": held, "exit": "TP", "entry": entry_price, "exit_price": current})
                if verbose:
                    print(f"  TP   @ ${current:.4f} | P&L={pnl_pct:+.1f}% | held {held} ticks")
                in_position = False

            elif pnl_pct <= -stop_loss_pct:
                trades.append({"pnl_pct": pnl_pct, "held": held, "exit": "SL", "entry": entry_price, "exit_price": current})
                if verbose:
                    print(f"  SL   @ ${current:.4f} | P&L={pnl_pct:+.1f}% | held {held} ticks")
                in_position = False

            elif held > 36:  # time exit: ~18h at 30min intervals
                trades.append({"pnl_pct": pnl_pct, "held": held, "exit": "TIME", "entry": entry_price, "exit_price": current})
                if verbose:
                    print(f"  TIME @ ${current:.4f} | P&L={pnl_pct:+.1f}% | held {held} ticks")
                in_position = False

    if not trades:
        return {"symbol": symbol, "trades": 0, "error": "No trades triggered in this period"}

    wins   = [t for t in trades if t["pnl_pct"] > 0]
    losses = [t for t in trades if t["pnl_pct"] <= 0]
    total_pnl_pct = sum(t["pnl_pct"] for t in trades)
    gross_win  = sum(t["pnl_pct"] for t in wins)
    gross_loss = abs(sum(t["pnl_pct"] for t in losses))
    profit_factor = round(gross_win / gross_loss, 2) if gross_loss > 0 else float("inf")
    avg_hold  = sum(t["held"] for t in trades) / len(trades)
    total_pnl_usd = sum(t["pnl_pct"] / 100 * trade_size_usd for t in trades)

    return {
        "symbol":        symbol,
        "data_points":   len(prices),
        "trades":        len(trades),
        "wins":          len(wins),
        "losses":        len(losses),
        "win_rate":      round(len(wins) / len(trades) * 100, 1),
        "total_pnl_pct": round(total_pnl_pct, 2),
        "total_pnl_usd": round(total_pnl_usd, 2),
        "profit_factor": profit_factor,
        "avg_win_pct":   round(sum(t["pnl_pct"] for t in wins) / len(wins), 2) if wins else 0,
        "avg_loss_pct":  round(sum(t["pnl_pct"] for t in losses) / len(losses), 2) if losses else 0,
        "avg_hold_ticks": round(avg_hold, 1),
        "avg_hold_hours": round(avg_hold * 0.5, 1),
        "exit_breakdown": {
            "TP":   sum(1 for t in trades if t["exit"] == "TP"),
            "SL":   sum(1 for t in trades if t["exit"] == "SL"),
            "TIME": sum(1 for t in trades if t["exit"] == "TIME"),
        },
        "params": {
            "tp": take_profit_pct,
            "sl": stop_loss_pct,
            "max_rsi": max_rsi_buy,
            "momentum": momentum_threshold,
        }
    }


def run_all_symbols() -> None:
    """Run backtests on all symbols with stored history and print a summary."""
    path = "data/price_history.json"
    if not os.path.exists(path):
        print("No price history found. Run the bot for a while first.")
        return

    with open(path) as f:
        all_data = json.load(f)

    print(f"\n{'='*70}")
    print(f"{'BACKTEST RESULTS':^70}")
    print(f"{'='*70}")
    print(f"{'Symbol':<10} {'Trades':>6} {'Win%':>6} {'P&L%':>8} {'P&L$':>8} {'PF':>6} {'AvgH':>6}")
    print(f"{'-'*70}")

    for symbol in sorted(all_data.keys()):
        if symbol in {"USDC", "USDT", "DAI"}:
            continue
        result = run_backtest(symbol)
        if "error" in result:
            print(f"{symbol:<10} {'insufficient data':>50}")
            continue
        print(
            f"{symbol:<10} {result['trades']:>6} {result['win_rate']:>5.1f}% "
            f"{result['total_pnl_pct']:>7.1f}% ${result['total_pnl_usd']:>6.2f} "
            f"{result['profit_factor']:>6.2f} {result['avg_hold_hours']:>5.1f}h"
        )

    print(f"{'='*70}")
    print("Note: Backtests use stored price snapshots (30min intervals).")
    print("Results are indicative only — real execution includes slippage and fees.\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CryptoBot Backtester")
    parser.add_argument("--symbol", type=str, help="Symbol to backtest (e.g. VVV)")
    parser.add_argument("--tp", type=float, default=8.0, help="Take profit %")
    parser.add_argument("--sl", type=float, default=8.0, help="Stop loss %")
    parser.add_argument("--rsi", type=float, default=50.0, help="Max RSI for entry")
    parser.add_argument("--verbose", action="store_true", help="Show each trade")
    args = parser.parse_args()

    if args.symbol:
        result = run_backtest(
            args.symbol,
            take_profit_pct=args.tp,
            stop_loss_pct=args.sl,
            max_rsi_buy=args.rsi,
            verbose=args.verbose,
        )
        print(json.dumps(result, indent=2))
    else:
        run_all_symbols()
