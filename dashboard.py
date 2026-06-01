import bot.ssl_fix  # must be first
import os
import csv
import json
from datetime import datetime, timezone
from flask import Flask, render_template_string, jsonify, request, redirect
from dotenv import load_dotenv

load_dotenv()
os.makedirs("logs", exist_ok=True)
os.makedirs("records", exist_ok=True)
os.makedirs("data", exist_ok=True)

from bot.market import Market
from bot.portfolio import Portfolio
from bot.wallet import Wallet
from bot.positions import get_position_summary, get_realized_summary
from bot.config import BASE_RPC_URL, PRIVATE_KEY, TOKENS
from bot.blacklist import block, unblock, get_all as get_blacklist
from flask import Flask, render_template_string, jsonify, request, redirect, request, redirect
from web3 import Web3
import certifi, requests as req

app = Flask(__name__)

HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CryptoBot Dashboard</title>
<meta http-equiv="refresh" content="60">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #0f1117; color: #e2e8f0; min-height: 100vh; }
  .header { background: #1a1d2e; border-bottom: 1px solid #2d3748;
            padding: 16px 32px; display: flex; align-items: center; gap: 16px; }
  .header h1 { font-size: 1.3rem; font-weight: 700; color: #fff; }
  .badge { background: #22c55e22; color: #22c55e; border: 1px solid #22c55e44;
           padding: 3px 10px; border-radius: 20px; font-size: 0.72rem; font-weight: 600; }
  .container { max-width: 1500px; margin: 0 auto; padding: 20px 32px; }
  .row-label { font-size: 0.7rem; font-weight: 700; color: #475569; text-transform: uppercase;
               letter-spacing: 0.08em; margin: 20px 0 10px; }
  .grid { display: grid; gap: 12px; margin-bottom: 4px; }
  .grid-holdings { grid-template-columns: 1.6fr repeat(auto-fill, minmax(160px, 1fr)); }
  .grid-pnl      { grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); }
  .card { background: #1a1d2e; border: 1px solid #2d3748; border-radius: 10px; padding: 16px 18px; }
  .card.big { border-color: #3d4a6b; }
  .card .label { font-size: 0.7rem; color: #64748b; text-transform: uppercase;
                 letter-spacing: 0.05em; margin-bottom: 6px; }
  .card .value { font-size: 1.5rem; font-weight: 700; color: #fff; line-height: 1.2; }
  .card .value.sm { font-size: 1.1rem; }
  .card .sub  { font-size: 0.75rem; color: #64748b; margin-top: 4px; }
  .card .sub2 { font-size: 0.7rem;  color: #475569; margin-top: 2px; }
  .pos { color: #22c55e; }
  .neg { color: #ef4444; }
  .warn{ color: #f59e0b; }
  .section { background: #1a1d2e; border: 1px solid #2d3748; border-radius: 10px;
             padding: 18px 20px; margin-bottom: 16px; }
  .section h2 { font-size: 0.8rem; font-weight: 700; color: #94a3b8;
                text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 14px; }
  table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
  th { text-align: left; padding: 8px 10px; color: #475569; font-weight: 600;
       font-size: 0.7rem; text-transform: uppercase; border-bottom: 1px solid #2d3748; }
  td { padding: 10px 10px; border-bottom: 1px solid #1a1d2e; color: #e2e8f0; vertical-align: top; }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: #1e2235; }
  .pill { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 0.7rem; font-weight: 600; }
  .pill.success { background: #22c55e22; color: #22c55e; }
  .pill.failed  { background: #ef444422; color: #ef4444; }
  .pill.short   { background: #f59e0b22; color: #f59e0b; }
  .pill.long    { background: #6366f122; color: #818cf8; }
  .hash { font-family: monospace; font-size: 0.72rem; color: #475569; }
  .empty { color: #475569; text-align: center; padding: 24px; font-size: 0.82rem; }
  .refresh { font-size: 0.68rem; color: #475569; margin-left: auto; }
  .tag-tp  { color: #22c55e; font-size: 0.72rem; }
  .tag-sl  { color: #ef4444; font-size: 0.72rem; }
  .tag-unk { color: #475569; font-size: 0.72rem; }
</style>
</head>
<body>
<div class="header">
  <h1>CryptoBot</h1>
  <span class="badge">LIVE</span>
  <span class="refresh">Auto-refreshes every 60s &nbsp;|&nbsp; {{ stats.wallet_address[:8] }}...{{ stats.wallet_address[-6:] }}</span>
</div>
<div class="container">

  <!-- ROW 1: WHAT I HOLD -->
  <div class="row-label">What I Hold</div>
  <div class="grid grid-holdings">

    <div class="card big">
      <div class="label">Total Portfolio</div>
      <div class="value">${{ "%.2f"|format(stats.portfolio_usd) }}</div>
      <div class="sub">Deployed: ${{ "%.2f"|format(stats.deployed_usd) }} &nbsp;|&nbsp; Available: ${{ "%.2f"|format(stats.portfolio_usd - stats.deployed_usd) }}</div>
      <div class="sub2">{{ stats.open_positions }} open position{{ 's' if stats.open_positions != 1 else '' }}</div>
    </div>

    <div class="card">
      <div class="label">USDC Reserve</div>
      <div class="value sm {{ 'warn' if stats.usdc_pct < 30 else '' }}">
        ${{ "%.2f"|format(stats.usdc_usd) }}
      </div>
      <div class="sub">{{ "%.1f"|format(stats.usdc_pct) }}% of portfolio</div>
      <div class="sub2">{{ "%.2f"|format(stats.usdc_balance) }} USDC{{ ' — LOW' if stats.usdc_pct < 30 else '' }}</div>
    </div>

    <div class="card">
      <div class="label">ETH (Gas)</div>
      <div class="value sm {{ 'warn' if stats.eth_balance < 0.005 else '' }}">
        {{ "%.5f"|format(stats.eth_balance) }}
      </div>
      <div class="sub">${{ "%.2f"|format(stats.eth_balance * stats.eth_price) }}</div>
      <div class="sub2">{{ 'LOW — top up' if stats.eth_balance < 0.005 else 'Base network' }}</div>
    </div>

    {% for p in open_positions %}
    <div class="card">
      <div class="label">{{ p.symbol }}
        <span style="font-size:0.65rem;margin-left:4px;color:#475569">{{ p.amount_tokens|round(4) }} tokens</span>
      </div>
      <div class="value sm {{ 'pos' if p.gain_loss_pct >= 0 else 'neg' }}">
        {% if p.current_value > 0 %} ${{ "%.2f"|format(p.current_value) }}
        {% else %} ${{ "%.2f"|format(p.cost_basis_usd) }} {% endif %}
      </div>
      <div class="sub {{ 'pos' if p.gain_loss_pct >= 0 else 'neg' }}">
        {% if p.gain_loss_pct != 0 %}{{ "%+.2f"|format(p.gain_loss_pct) }}% (${{ "%+.2f"|format(p.gain_loss_usd) }})
        {% else %}Cost: ${{ "%.2f"|format(p.cost_basis_usd) }}{% endif %}
      </div>
      <div class="sub2">
        <span class="tag-tp">TP: {% if p.take_profit_price %}${{ "%.4f"|format(p.take_profit_price) }}{% else %}—{% endif %}</span>
        &nbsp;
        <span class="tag-sl">SL: {% if p.stop_loss_price %}${{ "%.4f"|format(p.stop_loss_price) }}{% else %}—{% endif %}</span>
      </div>
    </div>
    {% endfor %}

  </div>

  <!-- ROW 2: P&L & TAXES -->
  <div class="row-label" style="margin-top:20px">Profit, Loss &amp; Taxes</div>
  <div class="grid grid-pnl">

    <div class="card">
      <div class="label">Unrealized P&L</div>
      <div class="value {{ 'pos' if stats.unrealized_gain >= 0 else 'neg' }}">
        ${{ "%+.2f"|format(stats.unrealized_gain) }}
      </div>
      <div class="sub">Open positions</div>
    </div>

    <div class="card">
      <div class="label">Realized P&L</div>
      <div class="value {{ 'pos' if stats.realized_gain >= 0 else 'neg' }}">
        ${{ "%+.2f"|format(stats.realized_gain) }}
      </div>
      <div class="sub">{{ stats.total_trades }} closed trades</div>
    </div>

    <div class="card">
      <div class="label">Total P&L</div>
      <div class="value {{ 'pos' if (stats.unrealized_gain + stats.realized_gain) >= 0 else 'neg' }}">
        ${{ "%+.2f"|format(stats.unrealized_gain + stats.realized_gain) }}
      </div>
      <div class="sub">Realized + unrealized</div>
    </div>

    <div class="card">
      <div class="label">Short-term Gains</div>
      <div class="value sm {{ 'pos' if stats.short_term >= 0 else 'neg' }}">
        ${{ "%+.2f"|format(stats.short_term) }}
      </div>
      <div class="sub">Tax: ordinary income</div>
    </div>

    <div class="card">
      <div class="label">Long-term Gains</div>
      <div class="value sm {{ 'pos' if stats.long_term >= 0 else 'neg' }}">
        ${{ "%+.2f"|format(stats.long_term) }}
      </div>
      <div class="sub">Tax: capital gains rate</div>
    </div>

    <div class="card">
      <div class="label">Gas Fees Paid</div>
      <div class="value sm">${{ "%.2f"|format(stats.total_gas_usd) }}</div>
      <div class="sub">{{ stats.total_txns }} transactions total</div>
    </div>

  </div>

  <!-- OPEN POSITIONS DETAIL -->
  <div class="section" style="margin-top:20px">
    <h2>Open Positions — Detail</h2>
    {% if open_positions %}
    <table>
      <tr>
        <th>Token</th><th>Amount</th><th>Entry Price</th><th>Current Price</th>
        <th>Cost Basis</th><th>Current Value</th><th>P&L $</th><th>P&L %</th>
        <th>Take Profit</th><th>Stop Loss</th><th>Hold Window</th><th>Opened</th><th>Tx</th>
      </tr>
      {% for p in open_positions %}
      <tr>
        <td><strong>{{ p.symbol }}</strong></td>
        <td>{{ "%.6f"|format(p.amount_tokens) }}</td>
        <td>{% if p.entry_price > 0 %}${{ "%.6f"|format(p.entry_price) }}{% else %}<span class="tag-unk">unknown</span>{% endif %}</td>
        <td>{% if p.current_price > 0 %}${{ "%.6f"|format(p.current_price) }}{% else %}<span class="tag-unk">pending</span>{% endif %}</td>
        <td>{% if p.cost_basis_usd > 0 %}${{ "%.2f"|format(p.cost_basis_usd) }}{% else %}<span class="tag-unk">—</span>{% endif %}</td>
        <td>{% if p.current_value > 0 %}${{ "%.2f"|format(p.current_value) }}{% else %}<span class="tag-unk">—</span>{% endif %}</td>
        <td class="{{ 'pos' if p.gain_loss_usd >= 0 else ('neg' if p.gain_loss_usd < 0 else '') }}">
          {% if p.cost_basis_usd > 0 %}${{ "%+.2f"|format(p.gain_loss_usd) }}{% else %}—{% endif %}
        </td>
        <td class="{{ 'pos' if p.gain_loss_pct > 0 else ('neg' if p.gain_loss_pct < 0 else '') }}">
          {% if p.cost_basis_usd > 0 %}{{ "%+.2f"|format(p.gain_loss_pct) }}%{% else %}—{% endif %}
        </td>
        <td class="tag-tp">{% if p.take_profit_price %}${{ "%.6f"|format(p.take_profit_price) }}<br>(+{{ p.take_profit_pct }}%){% else %}—{% endif %}</td>
        <td class="tag-sl">{% if p.stop_loss_price %}${{ "%.6f"|format(p.stop_loss_price) }}<br>(-{{ p.stop_loss_pct }}%){% else %}—{% endif %}</td>
        <td {% if p.hours_remaining is not none and p.hours_remaining < 4 %}class="warn"{% endif %}>
          {% if p.hours_remaining is not none %}{% if p.hours_remaining < 0 %}Expired{% else %}{{ p.hours_remaining }}h left{% endif %}{% else %}—{% endif %}
        </td>
        <td style="color:#64748b;font-size:0.72rem">{{ p.date_opened[:16].replace('T',' ') }}</td>
        <td><a href="https://basescan.org/tx/{{ p.get('entry_tx','') }}" target="_blank" class="hash">{{ p.get('entry_tx','')[:8] }}...</a></td>
      </tr>
      {% endfor %}
    </table>
    {% else %}
    <div class="empty">No open positions</div>
    {% endif %}
  </div>

  <!-- WATCHLIST -->
  <div class="section">
    <h2>Agent Watchlist — Top 20 Being Monitored
      {% if cache_updated %}<span style="font-weight:400;color:#475569;font-size:0.7rem;margin-left:8px">Updated {{ cache_updated }}</span>{% endif %}
    </h2>
    {% if watchlist %}
    <table>
      <tr><th>Token</th><th>Price</th><th>1h</th><th>24h</th><th>Mkt Cap</th><th>Signals</th><th>Action</th></tr>
      {% for c in watchlist %}
      <tr style="{{ 'opacity:0.3' if c.blocked else '' }}">
        <td><strong>{{ c.symbol }}</strong><span style="color:#475569;font-size:0.72rem;display:block">{{ c.name }}</span></td>
        <td>${{ "%.4f"|format(c.price|float) }}</td>
        <td class="{{ 'pos' if c.change_1h|float >= 0 else 'neg' }}">{{ "%+.2f"|format(c.change_1h|float) }}%</td>
        <td class="{{ 'pos' if c.change_24h|float >= 0 else 'neg' }}">{{ "%+.2f"|format(c.change_24h|float) }}%</td>
        <td>${{ "%.0fM"|format(c.market_cap|float / 1e6) }}</td>
        <td style="font-size:0.75rem;color:#94a3b8">
          {% if c.blocked %}<span style="color:#ef4444">Blocked by you</span>
          {% elif c.signals %}{% for s in c.signals %}<span style="display:block">• {{ s }}</span>{% endfor %}
          {% else %}<span style="color:#475569">Monitoring</span>{% endif %}
        </td>
        <td>
          {% if c.blocked %}
          <form method="POST" action="/unblock" style="display:inline">
            <input type="hidden" name="symbol" value="{{ c.symbol }}">
            <button type="submit" style="background:#22c55e22;color:#22c55e;border:1px solid #22c55e44;padding:3px 10px;border-radius:6px;cursor:pointer;font-size:0.75rem">Unblock</button>
          </form>
          {% else %}
          <form method="POST" action="/block" style="display:inline">
            <input type="hidden" name="symbol" value="{{ c.symbol }}">
            <input type="hidden" name="cg_id"  value="{{ c.cg_id }}">
            <button type="submit" style="background:#ef444422;color:#ef4444;border:1px solid #ef444444;padding:3px 10px;border-radius:6px;cursor:pointer;font-size:0.75rem">Don't Buy</button>
          </form>
          {% endif %}
        </td>
      </tr>
      {% endfor %}
    </table>
    {% else %}
    <div class="empty">Populates after first bot tick</div>
    {% endif %}
  </div>

  <!-- CLOSED TRADES -->
  <div class="section">
    <h2>Closed Trades</h2>
    {% if closed_trades %}
    <table>
      <tr><th>Token</th><th>Opened</th><th>Closed</th><th>Amount</th>
          <th>Cost Basis</th><th>Proceeds</th><th>P&L $</th><th>P&L %</th><th>Days</th><th>Term</th><th>Exit Tx</th></tr>
      {% for t in closed_trades %}
      <tr>
        <td><strong>{{ t.token }}</strong></td>
        <td style="color:#64748b">{{ t.date_opened[:10] }}</td>
        <td style="color:#64748b">{{ t.date_closed[:10] }}</td>
        <td>{{ "%.4f"|format(t.amount_tokens|float) }}</td>
        <td>${{ "%.2f"|format(t.cost_basis_usd|float) }}</td>
        <td>${{ "%.2f"|format(t.proceeds_usd|float) }}</td>
        <td class="{{ 'pos' if t.gain_loss_usd|float >= 0 else 'neg' }}">${{ "%+.2f"|format(t.gain_loss_usd|float) }}</td>
        <td class="{{ 'pos' if t.gain_loss_pct|float >= 0 else 'neg' }}">{{ "%+.2f"|format(t.gain_loss_pct|float) }}%</td>
        <td>{{ t.hold_days }}</td>
        <td><span class="pill {{ t.term }}">{{ t.term }}</span></td>
        <td><a href="https://basescan.org/tx/{{ t.exit_tx }}" target="_blank" class="hash">{{ t.exit_tx[:10] }}...</a></td>
      </tr>
      {% endfor %}
    </table>
    {% else %}
    <div class="empty">No closed trades yet</div>
    {% endif %}
  </div>

  <!-- ALL TRANSACTIONS -->
  <div class="section">
    <h2>All Transactions</h2>
    {% if transactions %}
    <table>
      <tr><th>Date</th><th>Sold</th><th>Amount</th><th>Bought</th><th>Gas</th><th>Status</th><th>Tx</th></tr>
      {% for t in transactions %}
      <tr>
        <td style="color:#64748b;font-size:0.75rem">{{ t.date_utc }}</td>
        <td>{{ t.token_in }}</td>
        <td>${{ "%.2f"|format(t.amount_in|float) }}</td>
        <td>{{ t.token_out }}</td>
        <td style="color:#64748b">{{ t.gas_cost_eth }}</td>
        <td><span class="pill {{ 'success' if t.status == 'success' else 'failed' }}">{{ t.status }}</span></td>
        <td><a href="https://basescan.org/tx/{{ t.tx_hash }}" target="_blank" class="hash">{{ t.tx_hash[:12] }}...</a></td>
      </tr>
      {% endfor %}
    </table>
    {% else %}
    <div class="empty">No transactions yet</div>
    {% endif %}
  </div>

</div>
</body>
</html>
"""

def _load_csv(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


ERC20_ABI = [{"inputs": [{"name": "account", "type": "address"}],
              "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}],
              "stateMutability": "view", "type": "function"}]

import logging
_log = logging.getLogger("dashboard")

def _get_full_balances(w3, wallet_address: str, prices: dict) -> dict:
    """Return balances and USD values for all tokens including USDC."""
    balances = {}
    total_usd = 0.0

    # Native ETH (gas only)
    try:
        eth_bal = float(Web3.from_wei(w3.eth.get_balance(wallet_address), "ether"))
    except Exception as e:
        _log.error(f"ETH balance failed: {e}")
        eth_bal = 0.0
    eth_price = prices.get("WETH", 0) or prices.get("ETH", 0)
    eth_usd = eth_bal * eth_price
    balances["ETH"] = {"balance": eth_bal, "value_usd": eth_usd, "is_gas": True, "price": eth_price}
    total_usd += eth_usd

    # ERC-20 tokens
    for symbol, info in TOKENS.items():
        if symbol == "WETH":
            continue
        try:
            contract = w3.eth.contract(
                address=Web3.to_checksum_address(info["address"]), abi=ERC20_ABI
            )
            raw = contract.functions.balanceOf(wallet_address).call()
            bal = raw / (10 ** info["decimals"])
            # Stablecoins always $1
            price = 1.0 if symbol in {"USDC", "USDT", "DAI"} else prices.get(symbol, 0)
            val = bal * price
            balances[symbol] = {"balance": bal, "value_usd": val, "is_gas": False, "price": price}
            total_usd += val
            _log.info(f"Balance {symbol}: {bal:.4f} (${val:.2f})")
        except Exception as e:
            _log.error(f"Balance failed for {symbol}: {e}")
            balances[symbol] = {"balance": 0.0, "value_usd": 0.0, "is_gas": False, "price": 0.0}

    balances["_total_usd"] = total_usd
    return balances


@app.route("/")
def index():
    session = req.Session()
    session.verify = certifi.where()
    w3 = Web3(Web3.HTTPProvider(BASE_RPC_URL, session=session))

    market = Market()
    prices = market.get_all_prices()

    from eth_account import Account
    wallet_address = Account.from_key(PRIVATE_KEY).address

    balances   = _get_full_balances(w3, wallet_address, prices)
    open_pos   = get_position_summary(prices)
    realized   = get_realized_summary()
    closed     = _load_csv("records/realized_gains.csv")
    txns       = list(reversed(_load_csv("records/transactions.csv")))

    # Load agent watchlist from cache
    watchlist = []
    cache_updated = None
    if os.path.exists("data/screener_cache.json"):
        with open("data/screener_cache.json") as _f:
            cache = json.load(_f)
        cache_updated = cache.get("updated", "")[:16].replace("T", " ")
        bl = get_blacklist()
        blocked_syms = set(bl.get("symbols", []))
        for c in cache.get("watchlist", []):
            c["blocked"] = c.get("symbol", "").upper() in blocked_syms
            watchlist.append(c)

    unrealized   = sum(p["gain_loss_usd"] for p in open_pos)
    deployed_usd = sum(p["cost_basis_usd"] for p in open_pos)
    usdc_bal     = balances.get("USDC", {}).get("balance", 0.0)
    usdc_usd     = balances.get("USDC", {}).get("value_usd", 0.0)
    eth_bal      = balances.get("ETH", {}).get("balance", 0.0)
    eth_price    = prices.get("WETH", 0.0)
    total_usd    = balances.get("_total_usd", 0.0)
    usdc_pct     = (usdc_usd / total_usd * 100) if total_usd > 0 else 0

    # Gas fees paid
    total_gas_eth = sum(float(t.get("gas_cost_eth", 0) or 0) for t in txns)
    total_gas_usd = total_gas_eth * eth_price

    stats = {
        "portfolio_usd":   total_usd,
        "deployed_usd":    deployed_usd,
        "usdc_balance":    usdc_bal,
        "usdc_usd":        usdc_usd,
        "usdc_pct":        usdc_pct,
        "wallet_address":  wallet_address,
        "realized_gain":   realized["total_realized_gain_usd"],
        "short_term":      realized["short_term_gain_usd"],
        "long_term":       realized["long_term_gain_usd"],
        "total_trades":    realized["total_trades_closed"],
        "open_positions":  len(open_pos),
        "unrealized_gain": unrealized,
        "eth_balance":     eth_bal,
        "eth_price":       eth_price,
        "balances":        {k: v for k, v in balances.items() if not k.startswith("_")},
        "total_gas_usd":   round(total_gas_usd, 4),
        "total_txns":      len(txns),
    }

    return render_template_string(
        HTML,
        stats=stats,
        open_positions=open_pos,
        closed_trades=list(reversed(closed)),
        transactions=txns,
        watchlist=watchlist,
        cache_updated=cache_updated,
    )


@app.route("/block", methods=["POST"])
def block_token():
    symbol = request.form.get("symbol", "").upper()
    cg_id  = request.form.get("cg_id", "")
    if symbol:
        block(symbol, cg_id, reason="User blocked via dashboard")
    return redirect("/")


@app.route("/unblock", methods=["POST"])
def unblock_token():
    symbol = request.form.get("symbol", "").upper()
    if symbol:
        unblock(symbol)
    return redirect("/")


@app.route("/api/summary")
def api_summary():
    market = Market()
    prices = market.get_all_prices()
    return jsonify({
        "prices":    prices,
        "positions": get_position_summary(prices),
        "realized":  get_realized_summary(),
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
