import bot.ssl_fix  # must be first import — patches SSL before any HTTPS calls

import json
import os
import time
import certifi
import schedule
import requests
from datetime import datetime, timezone
from web3 import Web3
from dotenv import load_dotenv

from bot.config import BASE_RPC_URL, validate
from bot.logger import setup_logger
from bot.wallet import Wallet
from bot.market import Market
from bot.portfolio import Portfolio
from bot.executor import Executor
from bot.agent import TradingAgent
from bot.performance import get_tier
from bot import token_cache

load_dotenv()
logger = setup_logger("main")

os.makedirs("logs", exist_ok=True)
os.makedirs("records", exist_ok=True)
os.makedirs("data", exist_ok=True)


def _connect_rpc(max_retries: int = 5, delay: int = 10) -> Web3:
    """Connect to Base RPC with retries — handles temporary outages gracefully."""
    session = requests.Session()
    session.verify = certifi.where()
    for attempt in range(1, max_retries + 1):
        try:
            w3 = Web3(Web3.HTTPProvider(BASE_RPC_URL, session=session))
            if w3.is_connected():
                logger.info(f"Connected to Base chain (block {w3.eth.block_number})")
                return w3
        except Exception as e:
            logger.warning(f"RPC connection attempt {attempt}/{max_retries} failed: {e}")
        if attempt < max_retries:
            time.sleep(delay)
    logger.error(f"Could not connect to Base RPC after {max_retries} attempts")
    raise SystemExit(1)


def _backfill_position_cg_ids():
    """
    Global startup fix: for any open position missing a cg_id,
    find it in the token cache by symbol and patch it in.
    Ensures all positions are priceable and trackable — runs every startup.
    """
    pos_file = "data/positions.json"
    if not os.path.exists(pos_file):
        return
    with open(pos_file) as f:
        all_pos = json.load(f)
    changed = False
    cache_all = token_cache.list_all()
    # Build reverse lookup: symbol → cg_id
    sym_to_cg = {
        info.get("symbol", "").upper(): cg_id
        for cg_id, info in cache_all.items()
        if info.get("symbol")
    }
    for symbol, lots in all_pos.items():
        cg_id = sym_to_cg.get(symbol.upper())
        if not cg_id:
            continue
        for lot in lots:
            if not lot.get("cg_id"):
                lot["cg_id"] = cg_id
                changed = True
                logger.info(f"Backfilled cg_id for {symbol}: {cg_id}")
    if changed:
        tmp = pos_file + ".tmp"
        with open(tmp, "w") as f:
            json.dump(all_pos, f, indent=2)
        os.replace(tmp, pos_file)


def _reconcile_positions(w3):
    """
    On startup: query on-chain balance for every recorded position.
    If the wallet holds zero of a token we think we own, remove that lot
    from positions.json so the bot doesn't keep trying to manage a ghost.
    Skips removal if the RPC call fails (don't nuke valid positions due to
    a connection hiccup).
    """
    pos_file = "data/positions.json"
    if not os.path.exists(pos_file):
        return
    with open(pos_file) as f:
        all_pos = json.load(f)

    from bot.config import TOKENS
    from bot import token_cache as _tc

    ERC20_ABI = [{
        "inputs":  [{"name": "account", "type": "address"}],
        "name":    "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "type":    "function",
    }]

    from eth_account import Account
    from bot.config import PRIVATE_KEY
    wallet = Account.from_key(PRIVATE_KEY).address

    changed = False
    for symbol in list(all_pos.keys()):
        addr     = TOKENS.get(symbol, {}).get("address")
        decimals = TOKENS.get(symbol, {}).get("decimals", 18)
        if not addr:
            cached = _tc.get_by_symbol(symbol)
            if cached:
                addr     = cached.get("address")
                decimals = cached.get("decimals", 18)
        if not addr:
            logger.warning(f"Reconcile: no contract address for {symbol}, skipping")
            continue
        try:
            contract  = w3.eth.contract(address=w3.to_checksum_address(addr), abi=ERC20_ABI)
            bal_wei   = contract.functions.balanceOf(w3.to_checksum_address(wallet)).call()
            bal_units = bal_wei / (10 ** decimals)
        except Exception as e:
            logger.warning(f"Reconcile: could not query {symbol} balance — skipping ({e})")
            continue

        recorded = sum(lot["amount_tokens"] for lot in all_pos[symbol])
        if bal_units < recorded * 0.01:  # on-chain balance < 1% of what we think we hold
            logger.error(
                f"Reconcile: {symbol} on-chain balance {bal_units:.6f} vs recorded "
                f"{recorded:.6f} — removing ghost position"
            )
            del all_pos[symbol]
            changed = True
        elif abs(bal_units - recorded) / max(recorded, 1e-9) > 0.1:
            logger.warning(
                f"Reconcile: {symbol} mismatch — on-chain {bal_units:.6f} "
                f"vs recorded {recorded:.6f} (>10% diff, investigate)"
            )

    if changed:
        tmp = pos_file + ".tmp"
        with open(tmp, "w") as f:
            json.dump(all_pos, f, indent=2)
        os.replace(tmp, pos_file)
        logger.info("Reconcile: positions.json updated")


LAST_TICK_FILE = "data/last_tick.json"


def _record_tick():
    """Record the current time as the last successful tick."""
    os.makedirs("data", exist_ok=True)
    with open(LAST_TICK_FILE, "w") as f:
        json.dump({"ts": datetime.now(timezone.utc).isoformat()}, f)


def _verify_unknown_sells(w3):
    """
    Re-check any sell transactions that timed out waiting for a receipt.
    If the tx succeeded on-chain, close the position and record the realized gain.
    Runs every startup so no successful sell ever leaves a ghost position.
    """
    import csv as _csv
    from bot import positions as _pos, capital as _cap

    tx_file = "records/transactions.csv"
    if not os.path.exists(tx_file):
        return

    with open(tx_file, newline="") as f:
        rows = list(_csv.DictReader(f))

    STABLECOINS = {"USDC", "USDT", "DAI"}
    USDC_ADDR = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
    TRANSFER_SIG = "ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

    from eth_account import Account
    from bot.config import PRIVATE_KEY
    wallet_addr = Account.from_key(PRIVATE_KEY).address

    changed = False
    for row in rows:
        if row.get("status") != "unknown":
            continue
        token_in  = row.get("token_in", "")
        token_out = row.get("token_out", "")
        tx_hash   = row.get("tx_hash", "")
        if not tx_hash or token_in.upper() in STABLECOINS or token_out.upper() not in STABLECOINS:
            continue

        try:
            full_hash = tx_hash if tx_hash.startswith("0x") else "0x" + tx_hash
            receipt   = w3.eth.get_transaction_receipt(full_hash)
        except Exception as e:
            logger.warning(f"verify_unknown_sells: receipt lookup failed for {tx_hash[:12]}: {e}")
            continue

        if receipt is None:
            continue

        new_status = "success" if receipt["status"] == 1 else "failed"
        row["status"]   = new_status
        row["gas_used"] = str(receipt["gasUsed"])
        changed = True
        logger.info(f"verify_unknown_sells: tx {tx_hash[:12]} confirmed {new_status}")

        if receipt["status"] != 1:
            continue

        # Decode actual USDC received from Transfer events
        usdc_received = 0.0
        for log in receipt["logs"]:
            if (log["address"].lower() == USDC_ADDR.lower()
                    and len(log["topics"]) >= 3
                    and log["topics"][0].hex() == TRANSFER_SIG):
                to_addr = "0x" + log["topics"][2].hex()[-40:]
                if to_addr.lower() == wallet_addr.lower():
                    usdc_received = int(log["data"].hex(), 16) / 1e6

        try:
            amount_in_tokens = float(row.get("amount_in", 0))
        except ValueError:
            logger.warning(f"verify_unknown_sells: bad amount_in in {tx_hash[:12]}")
            continue

        if amount_in_tokens <= 0 or usdc_received <= 0:
            logger.warning(f"verify_unknown_sells: cannot compute exit price for {tx_hash[:12]}")
            continue

        exit_price = usdc_received / amount_in_tokens
        try:
            realized = _pos.close_position(
                symbol=token_in,
                amount_tokens=amount_in_tokens,
                exit_price_usd=exit_price,
                exit_tx=tx_hash,
            )
            for r in realized:
                gain_usd = r["gain_loss_usd"]
                logger.info(
                    f"verify_unknown_sells: closed {token_in} | "
                    f"P&L: ${gain_usd:+.2f} ({r['gain_loss_pct']:+.2f}%) | "
                    f"held {r['hold_days']} days"
                )
                if gain_usd > 0:
                    _cap.lock_profit(gain_usd)
        except Exception as e:
            logger.error(f"verify_unknown_sells: close_position failed for {token_in}: {e}")

    if changed:
        fieldnames = list(rows[0].keys()) if rows else []
        tmp = tx_file + ".tmp"
        with open(tmp, "w", newline="") as f:
            writer = _csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(tmp, tx_file)


def main():
    validate()

    w3 = _connect_rpc()

    # Global startup: ensure all positions have cg_id for price tracking
    _backfill_position_cg_ids()
    # Close any positions whose sell tx succeeded but timed out before receipt
    _verify_unknown_sells(w3)
    # Remove ghost positions where wallet balance is confirmed zero on-chain
    _reconcile_positions(w3)

    wallet = Wallet(w3)
    market = Market(w3)
    portfolio = Portfolio(w3, wallet, market)
    executor = Executor(w3, wallet)
    agent = TradingAgent(portfolio, executor)

    logger.info("Trading agent started — interval adapts to performance tier")

    def run_and_reschedule():
        """Run one agent tick, then reschedule based on current performance tier."""
        prices = Market().get_all_prices()
        tier   = get_tier(prices)
        agent.current_tier = tier
        agent.run_once()
        # Tick succeeded — record timestamp and clear any credit alert
        _record_tick()
        _alert_file = "data/credit_alert.json"
        if os.path.exists(_alert_file):
            os.remove(_alert_file)

        # Adaptive interval: if a near-miss opportunity exists (score ≥ 45 but below entry
        # threshold), check back in 30min even if the tier normally waits longer.
        # This lets the bot catch setups that need just one more confirming candle.
        OPPORTUNITY_INTERVAL = 1800  # 30 minutes
        OPPORTUNITY_THRESHOLD = 45
        next_interval = tier["interval_seconds"]
        best_score = getattr(agent, "last_best_signal_score", 0)
        if best_score >= OPPORTUNITY_THRESHOLD and next_interval > OPPORTUNITY_INTERVAL:
            next_interval = OPPORTUNITY_INTERVAL
            logger.info(
                f"Near-miss opportunity (score {best_score}/100) — shortening next tick to 30min"
            )

        schedule.clear()
        schedule.every(next_interval).seconds.do(run_and_reschedule)
        logger.info(f"Next tick in {next_interval//60}min (tier: {tier['label']})")

    # First run immediately
    tier = get_tier(Market().get_all_prices())
    agent.current_tier = tier
    agent.run_once()
    _record_tick()

    schedule.every(tier["interval_seconds"]).seconds.do(run_and_reschedule)

    while True:
        schedule.run_pending()
        time.sleep(1)


if __name__ == "__main__":
    RESTART_DELAY = 30
    while True:
        try:
            main()
        except SystemExit:
            break
        except KeyboardInterrupt:
            logger.info("Bot stopped by user.")
            break
        except Exception as e:
            logger.error(f"Bot crashed: {e} — restarting in {RESTART_DELAY}s...")
            # Flag credit exhaustion so dashboard can alert the user
            if "credit balance is too low" in str(e):
                os.makedirs("data", exist_ok=True)
                with open("data/credit_alert.json", "w") as _f:
                    json.dump({"ts": datetime.now(timezone.utc).isoformat(), "active": True}, _f)
            time.sleep(RESTART_DELAY)
