import bot.ssl_fix  # must be first
import os
import csv
import json
from datetime import datetime, timezone
from flask import Flask, render_template_string, jsonify
from dotenv import load_dotenv

load_dotenv()
os.makedirs("logs", exist_ok=True)
os.makedirs("records", exist_ok=True)
os.makedirs("data", exist_ok=True)

from bot.market import Market
from bot.positions import get_position_summary, get_realized_summary, get_open_positions
from bot.config import BASE_RPC_URL, PRIVATE_KEY
from bot.wallet import Wallet
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
            padding: 20px 32px; display: flex; align-items: center; gap: 16px; }
  .header h1 { font-size: 1.4rem; font-weight: 700; color: #fff; }
  .badge { background: #22c55e22; color: #22c55e; border: 1px solid #22c55e44;
           padding: 4px 12px; border-radius: 20px; font-size: 0.75rem; font-weight: 600; }
  .badge.warn { background: #f59e0b22; color: #f59e0b; border-color: #f59e0b44; }
  .container { max-width: 1400px; margin: 0 auto; padding: 24px 32px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin-bottom: 24px; }
  .card { background: #1a1d2e; border: 1px solid #2d3748; border-radius: 12px; padding: 20px; }
  .card .label { font-size: 0.75rem; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 8px; }
  .card .value { font-size: 1.6rem; font-weight: 700; color: #fff; }
  .card .sub { font-size: 0.8rem; color: #64748b; margin-top: 4px; }
  .pos { color: #22c55e; }
  .neg { color: #ef4444; }
  .neutral { color: #94a3b8; }
  .section { background: #1a1d2e; border: 1px solid #2d3748; border-radius: 12px;
             padding: 20px; margin-bottom: 20px; }
  .section h2 { font-size: 0.9rem; font-weight: 600; color: #94a3b8;
                text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 16px; }
  table { width: 100%; border-collapse: collapse; font-size: 0.875rem; }
  th { text-align: left; padding: 10px 12px; color: #64748b; font-weight: 500;
       font-size: 0.75rem; text-transform: uppercase; border-bottom: 1px solid #2d3748; }
  td { padding: 12px 12px; border-bottom: 1px solid #1e2235; color: #e2e8f0; }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: #1e2235; }
  .pill { display: inline-block; padding: 2px 10px; border-radius: 12px;
          font-size: 0.75rem; font-weight: 600; }
  .pill.short { background: #f59e0b22; color: #f59e0b; }
  .pill.long  { background: #6366f122; color: #818cf8; }
  .pill.open  { background: #22c55e22; color: #22c55e; }
  .hash { font-family: monospace; font-size: 0.75rem; color: #64748b; }
  .empty { color: #475569; text-align: center; padding: 32px; font-size: 0.875rem; }
  .refresh { font-size: 0.7rem; color: #475569; margin-left: auto; }
</style>
</head>
<body>
<div class="header">
  <h1>⚡ CryptoBot</h1>
  <span class="badge" id="status">LIVE</span>
  <span class="refresh">Auto-refreshes every 60s</span>
</div>
<div class="container">

  <div class="grid">
    <div class="card">
      <div class="label">Portfolio Value</div>
      <div class="value">${{ "%.2f"|format(stats.portfolio_usd) }}</div>
      <div class="sub">{{ stats.wallet_address[:6] }}...{{ stats.wallet_address[-4:] }}</div>
    </div>
    <div class="card">
      <div class="label">Realized P&L</div>
      <div class="value {{ 'pos' if stats.realized_gain >= 0 else 'neg' }}">
        ${{ "%+.2f"|format(stats.realized_gain) }}
      </div>
      <div class="sub">{{ stats.total_trades }} closed trades</div>
    </div>
    <div class="card">
      <div class="label">Unrealized P&L</div>
      <div class="value {{ 'pos' if stats.unrealized_gain >= 0 else 'neg' }}">
        ${{ "%+.2f"|format(stats.unrealized_gain) }}
      </div>
      <div class="sub">{{ stats.open_positions }} open positions</div>
    </div>
    <div class="card">
      <div class="label">ETH Balance (gas)</div>
      <div class="value {{ 'warn' if stats.eth_balance < 0.005 else '' }}"
           style="{{ 'color:#f59e0b' if stats.eth_balance < 0.005 else '' }}">
        {{ "%.5f"|format(stats.eth_balance) }}
      </div>
      <div class="sub">{{ "LOW — top up needed" if stats.eth_balance < 0.005 else "Base network" }}</div>
    </div>
    <div class="card">
      <div class="label">Short-term Gains</div>
      <div class="value {{ 'pos' if stats.short_term >= 0 else 'neg' }}">
        ${{ "%+.2f"|format(stats.short_term) }}
      </div>
      <div class="sub">Tax: ordinary income rate</div>
    </div>
    <div class="card">
      <div class="label">Long-term Gains</div>
      <div class="value {{ 'pos' if stats.long_term >= 0 else 'neg' }}">
        ${{ "%+.2f"|format(stats.long_term) }}
      </div>
      <div class="sub">Tax: capital gains rate</div>
    </div>
  </div>

  <!-- Open Positions -->
  <div class="section">
    <h2>Open Positions</h2>
    {% if open_positions %}
    <table>
      <tr>
        <th>Token</th><th>Amount</th><th>Entry Price</th>
        <th>Current Price</th><th>Cost Basis</th><th>Current Value</th>
        <th>P&L ($)</th><th>P&L (%)</th><th>Days Held</th><th>Entry Tx</th>
      </tr>
      {% for p in open_positions %}
      <tr>
        <td><strong>{{ p.symbol }}</strong></td>
        <td>{{ "%.6f"|format(p.amount_tokens) }}</td>
        <td>${{ "%.4f"|format(p.entry_price) }}</td>
        <td>${{ "%.4f"|format(p.current_price) }}</td>
        <td>${{ "%.2f"|format(p.cost_basis_usd) }}</td>
        <td>${{ "%.2f"|format(p.current_value) }}</td>
        <td class="{{ 'pos' if p.gain_loss_usd >= 0 else 'neg' }}">
          ${{ "%+.2f"|format(p.gain_loss_usd) }}
        </td>
        <td class="{{ 'pos' if p.gain_loss_pct >= 0 else 'neg' }}">
          {{ "%+.2f"|format(p.gain_loss_pct) }}%
        </td>
        <td>{{ p.hold_days }}</td>
        <td><a href="https://basescan.org/tx/{{ p.get('entry_tx','') }}"
               target="_blank" class="hash">{{ p.get('entry_tx','')[:10] }}...</a></td>
      </tr>
      {% endfor %}
    </table>
    {% else %}
    <div class="empty">No open positions</div>
    {% endif %}
  </div>

  <!-- Closed Trades -->
  <div class="section">
    <h2>Closed Trades (Realized)</h2>
    {% if closed_trades %}
    <table>
      <tr>
        <th>Token</th><th>Opened</th><th>Closed</th><th>Amount</th>
        <th>Cost Basis</th><th>Proceeds</th><th>P&L ($)</th>
        <th>P&L (%)</th><th>Days</th><th>Term</th><th>Exit Tx</th>
      </tr>
      {% for t in closed_trades %}
      <tr>
        <td><strong>{{ t.token }}</strong></td>
        <td>{{ t.date_opened[:10] }}</td>
        <td>{{ t.date_closed[:10] }}</td>
        <td>{{ "%.6f"|format(t.amount_tokens|float) }}</td>
        <td>${{ "%.2f"|format(t.cost_basis_usd|float) }}</td>
        <td>${{ "%.2f"|format(t.proceeds_usd|float) }}</td>
        <td class="{{ 'pos' if t.gain_loss_usd|float >= 0 else 'neg' }}">
          ${{ "%+.2f"|format(t.gain_loss_usd|float) }}
        </td>
        <td class="{{ 'pos' if t.gain_loss_pct|float >= 0 else 'neg' }}">
          {{ "%+.2f"|format(t.gain_loss_pct|float) }}%
        </td>
        <td>{{ t.hold_days }}</td>
        <td><span class="pill {{ t.term }}">{{ t.term }}</span></td>
        <td><a href="https://basescan.org/tx/{{ t.exit_tx }}"
               target="_blank" class="hash">{{ t.exit_tx[:10] }}...</a></td>
      </tr>
      {% endfor %}
    </table>
    {% else %}
    <div class="empty">No closed trades yet</div>
    {% endif %}
  </div>

  <!-- All Transactions -->
  <div class="section">
    <h2>All Transactions</h2>
    {% if transactions %}
    <table>
      <tr>
        <th>Date</th><th>Type</th><th>Sold</th><th>Amount In</th>
        <th>Bought</th><th>Amount Out</th><th>Gas (ETH)</th>
        <th>Status</th><th>Tx Hash</th>
      </tr>
      {% for t in transactions %}
      <tr>
        <td>{{ t.date_utc }}</td>
        <td>{{ t.type }}</td>
        <td>{{ t.token_in }}</td>
        <td>{{ "%.6f"|format(t.amount_in|float) }}</td>
        <td>{{ t.token_out }}</td>
        <td>{{ t.amount_out }}</td>
        <td>{{ t.gas_cost_eth }}</td>
        <td><span class="pill {{ 'open' if t.status == 'success' else 'short' }}">
          {{ t.status }}</span></td>
        <td><a href="https://basescan.org/tx/{{ t.tx_hash }}"
               target="_blank" class="hash">{{ t.tx_hash[:16] }}...</a></td>
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


def _get_portfolio_usd(w3, wallet_address: str, prices: dict) -> float:
    try:
        eth_bal = float(Web3.from_wei(w3.eth.get_balance(wallet_address), "ether"))
        return eth_bal * prices.get("WETH", 0)
    except Exception:
        return 0.0


def _get_eth_balance(w3, wallet_address: str) -> float:
    try:
        return float(Web3.from_wei(w3.eth.get_balance(wallet_address), "ether"))
    except Exception:
        return 0.0


@app.route("/")
def index():
    session = req.Session()
    session.verify = certifi.where()
    w3 = Web3(Web3.HTTPProvider(BASE_RPC_URL, session=session))

    market = Market()
    prices = market.get_all_prices()

    from eth_account import Account
    wallet_address = Account.from_key(PRIVATE_KEY).address

    open_pos   = get_position_summary(prices)
    realized   = get_realized_summary()
    closed     = _load_csv("records/realized_gains.csv")
    txns       = list(reversed(_load_csv("records/transactions.csv")))

    unrealized = sum(p["gain_loss_usd"] for p in open_pos)

    stats = {
        "portfolio_usd":  _get_portfolio_usd(w3, wallet_address, prices),
        "wallet_address": wallet_address,
        "realized_gain":  realized["total_realized_gain_usd"],
        "short_term":     realized["short_term_gain_usd"],
        "long_term":      realized["long_term_gain_usd"],
        "total_trades":   realized["total_trades_closed"],
        "open_positions": len(open_pos),
        "unrealized_gain": unrealized,
        "eth_balance":    _get_eth_balance(w3, wallet_address),
    }

    return render_template_string(
        HTML,
        stats=stats,
        open_positions=open_pos,
        closed_trades=list(reversed(closed)),
        transactions=txns,
    )


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
