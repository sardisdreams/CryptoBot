import bot.ssl_fix  # must be first
import os
import csv
import json
from datetime import datetime, timezone
from functools import wraps
from flask import Flask, render_template_string, jsonify, request, redirect, Response
from dotenv import load_dotenv

load_dotenv()
os.makedirs("logs", exist_ok=True)
os.makedirs("records", exist_ok=True)
os.makedirs("data", exist_ok=True)

from bot.market import Market
from bot.portfolio import Portfolio
from bot.wallet import Wallet
from bot.positions import get_position_summary, get_realized_summary
from bot.config import BASE_RPC_URL, TOKENS, BOT_VERSION
from bot import capital, knowledge as knowledge_module

def _get_wallet_address() -> str:
    """Derive wallet address from private key without holding the key in module scope."""
    from eth_account import Account as _Account
    from bot.config import PRIVATE_KEY as _PK
    if not _PK:
        return "0x0000000000000000000000000000000000000000"
    return _Account.from_key(_PK).address
from bot.blacklist import block, unblock, get_all as get_blacklist
from bot.cost_tracker import get_summary as get_cost_summary
from flask import Flask, render_template_string, jsonify, request, redirect, request, redirect
from web3 import Web3
import certifi, requests as req

app = Flask(__name__)

# Basic auth — set DASHBOARD_USER and DASHBOARD_PASS in .env (optional, skip locally)
DASH_USER = os.getenv("DASHBOARD_USER", "")
DASH_PASS = os.getenv("DASHBOARD_PASS", "")

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not DASH_USER or not DASH_PASS:
            return f(*args, **kwargs)  # no auth configured — allow all
        auth = request.authorization
        if not auth or auth.username != DASH_USER or auth.password != DASH_PASS:
            return Response(
                "Authentication required", 401,
                {"WWW-Authenticate": 'Basic realm="CryptoBot"'}
            )
        return f(*args, **kwargs)
    return decorated

HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CryptoBot Dashboard</title>
<meta http-equiv="refresh" content="30">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #13161f; color: #e2e8f0; min-height: 100vh; }
  .header { background: #1e2236; border-bottom: 1px solid #3d4a5c;
            padding: 16px 32px; display: flex; align-items: center; gap: 12px; }
  .header h1 { font-size: 1.3rem; font-weight: 700; color: #fff; }
  .badge { background: #22c55e22; color: #22c55e; border: 1px solid #22c55e44;
           padding: 3px 10px; border-radius: 20px; font-size: 0.72rem; font-weight: 600; }
  .ver { background: #6366f122; color: #818cf8; border: 1px solid #6366f144;
         padding: 3px 10px; border-radius: 20px; font-size: 0.72rem; font-weight: 600; }
  /* Tabs */
  .tabs { display: flex; gap: 0; border-bottom: 1px solid #3d4a5c;
          background: #1e2236; padding: 0 32px; }
  .tab  { padding: 12px 20px; font-size: 0.82rem; font-weight: 600; color: #64748b;
          cursor: pointer; border-bottom: 2px solid transparent; transition: all 0.15s; }
  .tab:hover { color: #94a3b8; }
  .tab.active { color: #e2e8f0; border-bottom-color: #6366f1; }
  .tab-content { display: none; }
  .tab-content.active { display: block; }
  .container { max-width: 1500px; margin: 0 auto; padding: 20px 32px; }
  .row-label { font-size: 0.68rem; font-weight: 700; color: #475569; text-transform: uppercase;
               letter-spacing: 0.08em; margin: 18px 0 8px; }
  .grid { display: grid; gap: 10px; margin-bottom: 4px; }
  .grid-top  { grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); }
  .grid-pnl  { grid-template-columns: repeat(auto-fill, minmax(155px, 1fr)); }
  .grid-pos  { grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); }
  .card { background: #1e2236; border: 1px solid #3d4a5c; border-radius: 10px; padding: 14px 16px; }
  .card .label { font-size: 0.68rem; color: #64748b; text-transform: uppercase;
                 letter-spacing: 0.05em; margin-bottom: 5px; }
  .card .value { font-size: 1.4rem; font-weight: 700; color: #fff; line-height: 1.2; }
  .card .value.sm { font-size: 1.05rem; }
  .card .sub  { font-size: 0.72rem; color: #64748b; margin-top: 3px; }
  .card .sub2 { font-size: 0.68rem; color: #475569; margin-top: 2px; }
  .pos { color: #22c55e; }
  .neg { color: #ef4444; }
  .warn{ color: #f59e0b; }
  .section { background: #1e2236; border: 1px solid #3d4a5c; border-radius: 10px;
             padding: 16px 18px; margin-bottom: 14px; }
  .section h2 { font-size: 0.78rem; font-weight: 700; color: #94a3b8;
                text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 12px; }
  table { width: 100%; border-collapse: collapse; font-size: 0.8rem; }
  table.positions-table { font-size: 0.9rem; }
  th { text-align: left; padding: 7px 9px; color: #475569; font-weight: 600;
       font-size: 0.68rem; text-transform: uppercase; border-bottom: 1px solid #3d4a5c; }
  td { padding: 9px 9px; border-bottom: 1px solid #2a3347; color: #e2e8f0; vertical-align: top; }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: #1e2235; }
  .pill { display: inline-block; padding: 2px 7px; border-radius: 9px; font-size: 0.68rem; font-weight: 600; }
  .pill.success { background: #22c55e22; color: #22c55e; }
  .pill.failed  { background: #ef444422; color: #ef4444; }
  .pill.short   { background: #f59e0b22; color: #f59e0b; }
  .pill.long    { background: #6366f122; color: #818cf8; }
  .hash { font-family: monospace; font-size: 0.7rem; color: #475569; }
  .empty { color: #475569; text-align: center; padding: 20px; font-size: 0.8rem; }
  .refresh { font-size: 0.66rem; color: #475569; margin-left: auto; }
  .tag-tp  { color: #22c55e; font-size: 0.7rem; }
  .tag-sl  { color: #ef4444; font-size: 0.7rem; }
  .tag-unk { color: #475569; font-size: 0.7rem; }
  a.cglink { color: #94a3b8; text-decoration: none; font-weight: 600; }
  a.cglink:hover { color: #e2e8f0; text-decoration: underline; }
</style>
</head>
<body>
<div class="header">
  <h1>CryptoBot</h1>
  <span class="badge">LIVE</span>
  <span class="ver">v2.12</span>
  <span class="ver">Bot {{ bot_version }}</span>
  <span class="refresh">Auto-refreshes every 60s &nbsp;|&nbsp; {{ stats.wallet_address[:8] }}...{{ stats.wallet_address[-6:] }}</span>
</div>

<!-- TABS -->
<div class="tabs">
  <div class="tab active" onclick="showTab('portfolio')">Portfolio</div>
  <div class="tab" onclick="showTab('watchlist')">Agent Watchlist</div>
  <div class="tab" onclick="showTab('history')">Trade History</div>
  <div class="tab" onclick="showTab('analytics')">Analytics</div>
  <div class="tab" onclick="showTab('knowledge')">Knowledge Base</div>
</div>

<!-- TAB: PORTFOLIO -->
<div id="tab-portfolio" class="tab-content active">
<div class="container">

  <!-- ROW 1: SUMMARY -->
  <div class="row-label">Summary</div>
  <div class="grid grid-top">
    <div class="card">
      <div class="label">Total Portfolio</div>
      <div class="value">${{ "%.2f"|format(stats.portfolio_usd) }}</div>
      <div class="sub">Deployed: ${{ "%.2f"|format(stats.deployed_usd) }}</div>
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
      <div class="sub2">{{ 'LOW — top up' if stats.eth_balance < 0.005 else 'Base network' }} | Gas: {{ stats.gas_price_gwei }} gwei</div>
    </div>
    <div class="card">
      <div class="label">Open Positions</div>
      <div class="value {{ 'pos' if stats.open_positions > 0 else '' }}">{{ stats.open_positions }}</div>
      <div class="sub">Active trades</div>
    </div>
    <div class="card">
      <div class="label">Closed Trades</div>
      <div class="value">{{ stats.total_trades }}</div>
      <div class="sub">All time</div>
    </div>
    <div class="card">
      <div class="label">Capital Floor</div>
      <div class="value sm">${{ "%.2f"|format(stats.capital_floor) }}</div>
      <div class="sub">Protected reserve</div>
      <div class="sub2">Max deploy: ${{ "%.2f"|format(stats.capital_max_deploy) }}</div>
    </div>
    <div class="card">
      <div class="label">Withdrawable</div>
      <div class="value sm pos">${{ "%.2f"|format(stats.capital_withdrawable) }}</div>
      <div class="sub">Locked profits (10%)</div>
      <div class="sub2">Base floor: $100</div>
    </div>
  </div>

  <!-- ROW 2: P&L & TAXES -->
  <div class="row-label">Profit, Loss &amp; Taxes</div>
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
      <div class="label">Gas Fees</div>
      <div class="value sm neg">-${{ "%.2f"|format(stats.total_gas_usd) }}</div>
      <div class="sub">{{ stats.total_txns }} transactions</div>
    </div>
    <div class="card">
      <div class="label">Claude API</div>
      <div class="value sm neg">-${{ "%.2f"|format(stats.anthropic_total) }}</div>
      <div class="sub">Today: -${{ "%.4f"|format(stats.anthropic_today) }}</div>
      <div class="sub2">7d: -${{ "%.2f"|format(stats.anthropic_7d) }} &nbsp;|&nbsp; Alchemy/CoinGecko: free</div>
    </div>
    <div class="card" style="border-color:#6366f144">
      <div class="label">Net Profit (after costs)</div>
      <div class="value {{ 'pos' if stats.net_profit >= 0 else 'neg' }}">
        ${{ "%+.2f"|format(stats.net_profit) }}
      </div>
      <div class="sub">P&L minus all fees</div>
      <div class="sub2">Total costs: ${{ "%.2f"|format(stats.total_costs) }}</div>
    </div>
  </div>

  <!-- ROW 3: CURRENT HOLDINGS -->
  <div class="row-label">Current Holdings</div>
  <div class="grid grid-pos">
    {% for p in open_positions %}
    <div class="card" style="{% if p.gain_loss_pct >= 0 %}background:#1a3d28;border-color:#1e8a3a;{% else %}background:#3d1618;border-color:#c5221f;{% endif %}">
      <div class="label">
        {% if p.cg_url %}<a href="{{ p.cg_url }}" target="_blank" class="cglink">{{ p.symbol }}</a>
        {% else %}{{ p.symbol }}{% endif %}
        <span style="font-size:0.62rem;color:#475569;margin-left:4px">{{ "%.4f"|format(p.amount_tokens) }}</span>
      </div>
      <div class="value sm {{ 'pos' if p.gain_loss_pct >= 0 else 'neg' }}">
        ${{ "%.2f"|format(p.current_value if p.current_value > 0 else p.cost_basis_usd) }}
      </div>
      <div class="sub {{ 'pos' if p.gain_loss_pct >= 0 else 'neg' }}">
        {% if p.cost_basis_usd > 0 %}{{ "%+.2f"|format(p.gain_loss_pct) }}% &nbsp; ${{ "%+.2f"|format(p.gain_loss_usd) }}
        {% else %}Cost: ${{ "%.2f"|format(p.cost_basis_usd) }}{% endif %}
      </div>
    </div>
    {% else %}
    <div style="color:#475569;font-size:0.8rem;padding:8px 0">No open positions</div>
    {% endfor %}
  </div>

  <!-- OPEN POSITIONS DETAIL -->
  <div class="section" style="margin-top:16px">
    <h2>Open Positions — Detail</h2>
    {% if open_positions %}
    <table class="positions-table">
      <tr>
        <th>Token</th><th>Amount</th><th>Entry</th><th>Current</th>
        <th>Cost</th><th>Value</th><th>P&L $</th><th>P&L %</th>
        <th>Take Profit</th><th>TP Profit</th><th>Stop Loss</th><th>SL Risk</th><th>Window</th>
      </tr>
      {% for p in open_positions %}
      <tr>
        <td>
          {% if p.cg_url %}<a href="{{ p.cg_url }}" target="_blank" class="cglink">{{ p.symbol }}</a>
          {% else %}<strong>{{ p.symbol }}</strong>{% endif %}
        </td>
        <td>{{ "%.4f"|format(p.amount_tokens) }}</td>
        <td>{% if p.entry_price > 0 %}${{ "%.6f"|format(p.entry_price) }}{% else %}<span class="tag-unk">unknown</span>{% endif %}</td>
        <td>{% if p.current_price > 0 %}${{ "%.6f"|format(p.current_price) }}{% else %}<span class="tag-unk">pending</span>{% endif %}</td>
        <td>{% if p.cost_basis_usd > 0 %}${{ "%.2f"|format(p.cost_basis_usd) }}{% else %}—{% endif %}</td>
        <td class="{{ 'pos' if p.current_value >= p.cost_basis_usd else 'neg' }}">{% if p.current_value > 0 %}${{ "%.2f"|format(p.current_value) }}{% else %}—{% endif %}</td>
        <td class="{{ 'pos' if p.gain_loss_usd >= 0 else 'neg' }}">
          {% if p.cost_basis_usd > 0 %}${{ "%+.2f"|format(p.gain_loss_usd) }}{% else %}—{% endif %}
        </td>
        <td class="{{ 'pos' if p.gain_loss_pct > 0 else 'neg' }}">
          {% if p.cost_basis_usd > 0 %}{{ "%+.2f"|format(p.gain_loss_pct) }}%{% else %}—{% endif %}
        </td>
        <td class="tag-tp">{% if p.take_profit_price %}${{ "%.6f"|format(p.take_profit_price) }}<br>(+{{ p.take_profit_pct }}%){% else %}—{% endif %}</td>
        <td>
          {% if p.take_profit_price and p.entry_price > 0 and p.cost_basis_usd > 0 %}
            {% set tp_profit = p.cost_basis_usd * p.take_profit_pct / 100 %}
            {% set progress = [(p.current_price - p.entry_price) / (p.take_profit_price - p.entry_price) * 100, 0] | max %}
            {% set progress = [progress, 100] | min %}
            <span class="pos" style="font-size:0.8rem;font-weight:700;">+${{ "%.2f"|format(tp_profit) }}</span><br>
            <div style="margin-top:4px;background:#22c55e22;border-radius:4px;height:6px;width:80px;">
              <div style="background:#22c55e;height:6px;border-radius:4px;width:{{ "%.0f"|format(progress) }}%;"></div>
            </div>
            <span class="pos" style="font-size:0.65rem;">{{ "%.0f"|format(progress) }}%</span>
          {% else %}—{% endif %}
        </td>
        <td class="tag-sl">{% if p.stop_loss_price %}${{ "%.6f"|format(p.stop_loss_price) }}<br>(-{{ p.stop_loss_pct }}%){% else %}—{% endif %}</td>
        <td>
          {% if p.stop_loss_price and p.entry_price > 0 and p.cost_basis_usd > 0 %}
            {% set sl_risk = p.cost_basis_usd * p.stop_loss_pct / 100 %}
            {% set sl_progress = [(p.entry_price - p.current_price) / (p.entry_price - p.stop_loss_price) * 100, 0] | max %}
            {% set sl_progress = [sl_progress, 100] | min %}
            <span class="neg" style="font-size:0.8rem;font-weight:700;">-${{ "%.2f"|format(sl_risk) }}</span><br>
            <div style="margin-top:4px;background:#ef444422;border-radius:4px;height:6px;width:80px;">
              <div style="background:#ef4444;height:6px;border-radius:4px;width:{{ "%.0f"|format(sl_progress) }}%;"></div>
            </div>
            <span class="neg" style="font-size:0.65rem;">{{ "%.0f"|format(sl_progress) }}%</span>
          {% else %}—{% endif %}
        </td>
        <td {% if p.hours_remaining is not none and p.hours_remaining < 4 %}class="warn"{% endif %}>
          {% if p.hours_remaining is not none %}{% if p.hours_remaining < 0 %}Expired{% else %}{{ p.hours_remaining }}h{% endif %}{% else %}—{% endif %}
        </td>
      </tr>
      {% endfor %}
    </table>
    {% else %}
    <div class="empty">No open positions</div>
    {% endif %}
  </div>

</div>
</div><!-- end tab-portfolio -->

<!-- TAB: WATCHLIST -->
<div id="tab-watchlist" class="tab-content">
<div class="container">
  <div class="section" style="margin-top:16px">
    <h2>Agent Watchlist — Top 20 Being Monitored
      {% if cache_updated %}<span style="font-weight:400;color:#475569;font-size:0.68rem;margin-left:8px">Updated {{ cache_updated }}</span>{% endif %}
    </h2>
    <p style="color:#64748b;font-size:0.78rem;margin-bottom:12px">Tokens the agent is watching for entry signals. Click Don't Buy to block a token permanently.</p>
    {% if watchlist %}
    <table>
      <tr><th>Token</th><th>Price</th><th>1h</th><th>24h</th><th>Mkt Cap</th><th>Signals</th><th>Action</th></tr>
      {% for c in watchlist %}
      <tr style="{{ 'opacity:0.3' if c.blocked else '' }}">
        <td>
          <a href="https://www.coingecko.com/en/coins/{{ c.cg_id }}" target="_blank" class="cglink">{{ c.symbol }}</a>
          <span style="color:#475569;font-size:0.7rem;display:block">{{ c.name }}</span>
        </td>
        <td>${{ "%.4f"|format(c.price|float) }}</td>
        <td class="{{ 'pos' if c.change_1h|float >= 0 else 'neg' }}">{{ "%+.2f"|format(c.change_1h|float) }}%</td>
        <td class="{{ 'pos' if c.change_24h|float >= 0 else 'neg' }}">{{ "%+.2f"|format(c.change_24h|float) }}%</td>
        <td>${{ "%.0fM"|format(c.market_cap|float / 1e6) }}</td>
        <td style="font-size:0.73rem;color:#94a3b8">
          {% if c.blocked %}<span style="color:#ef4444">Blocked</span>
          {% elif c.signals %}{% for s in c.signals %}<span style="display:block">• {{ s }}</span>{% endfor %}
          {% else %}<span style="color:#475569">Monitoring</span>{% endif %}
        </td>
        <td>
          {% if c.blocked %}
          <form method="POST" action="/unblock" style="display:inline">
            <input type="hidden" name="symbol" value="{{ c.symbol }}">
            <button type="submit" style="background:#22c55e22;color:#22c55e;border:1px solid #22c55e44;padding:3px 9px;border-radius:5px;cursor:pointer;font-size:0.73rem">Unblock</button>
          </form>
          {% else %}
          <form method="POST" action="/block" style="display:inline">
            <input type="hidden" name="symbol" value="{{ c.symbol }}">
            <input type="hidden" name="cg_id"  value="{{ c.cg_id }}">
            <button type="submit" style="background:#ef444422;color:#ef4444;border:1px solid #ef444444;padding:3px 9px;border-radius:5px;cursor:pointer;font-size:0.73rem">Don't Buy</button>
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
</div>
</div><!-- end tab-watchlist -->

<!-- TAB: HISTORY -->
<div id="tab-history" class="tab-content">
<div class="container">

  <div class="section" style="margin-top:16px">
    <h2>Closed Trades &nbsp;
      <input id="trade-filter" type="text" placeholder="Filter by token..." oninput="filterTrades()"
        style="font-size:0.75rem;padding:3px 8px;border-radius:6px;border:1px solid #3d4a5c;background:#13161f;color:#e2e8f0;margin-left:8px;">
    </h2>
    {% if closed_trades %}
    <table id="trade-table">
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

  <div class="section">
    <h2>All Transactions</h2>
    {% if transactions %}
    <table>
      <tr><th>Date</th><th>Sold</th><th>Amount</th><th>Bought</th><th>Gas</th><th>Status</th><th>Tx</th></tr>
      {% for t in transactions %}
      <tr>
        <td style="color:#64748b;font-size:0.73rem">{{ t.date_utc }}</td>
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
</div><!-- end tab-history -->

<!-- TAB: ANALYTICS -->
<div id="tab-analytics" class="tab-content">
<div class="container">
  <div class="row-label">Performance Summary</div>
  <div class="grid grid-top">
    <div class="card">
      <div class="label">Win Rate (all time)</div>
      <div class="value {{ 'pos' if analytics.win_rate >= 50 else 'neg' }}">{{ analytics.win_rate }}%</div>
      <div class="sub">{{ analytics.wins }}W / {{ analytics.losses }}L</div>
    </div>
    <div class="card">
      <div class="label">Avg Win</div>
      <div class="value sm pos">+${{ "%.2f"|format(analytics.avg_win) }}</div>
      <div class="sub">{{ "%.1f"|format(analytics.avg_win_pct) }}% avg gain</div>
    </div>
    <div class="card">
      <div class="label">Avg Loss</div>
      <div class="value sm neg">-${{ "%.2f"|format(analytics.avg_loss) }}</div>
      <div class="sub">{{ "%.1f"|format(analytics.avg_loss_pct) }}% avg loss</div>
    </div>
    <div class="card">
      <div class="label">Profit Factor</div>
      <div class="value sm {{ 'pos' if analytics.profit_factor >= 1 else 'neg' }}">{{ "%.2f"|format(analytics.profit_factor) }}x</div>
      <div class="sub">Gross wins / gross losses</div>
    </div>
    <div class="card">
      <div class="label">Best Trade</div>
      <div class="value sm pos">+${{ "%.2f"|format(analytics.best_trade_usd) }}</div>
      <div class="sub">{{ analytics.best_trade_token }}</div>
    </div>
    <div class="card">
      <div class="label">Worst Trade</div>
      <div class="value sm neg">-${{ "%.2f"|format(analytics.worst_trade_usd) }}</div>
      <div class="sub">{{ analytics.worst_trade_token }}</div>
    </div>
    <div class="card">
      <div class="label">Avg Hold Time</div>
      <div class="value sm">{{ analytics.avg_hold_days }}d</div>
      <div class="sub">Closed trades</div>
    </div>
    <div class="card">
      <div class="label">Current Streak</div>
      <div class="value sm {{ 'pos' if analytics.streak.startswith('W') else 'neg' }}">{{ analytics.streak }}</div>
      <div class="sub">Win/Loss streak</div>
    </div>
  </div>

  <div class="row-label">P&L by Token</div>
  <div class="section">
    {% if analytics.by_token %}
    <table>
      <tr><th>Token</th><th>Trades</th><th>W/L</th><th>Total P&L</th><th>Avg P&L %</th><th>Best</th><th>Worst</th></tr>
      {% for t in analytics.by_token %}
      <tr>
        <td><strong>{{ t.token }}</strong></td>
        <td>{{ t.count }}</td>
        <td class="{{ 'pos' if t.wins >= t.losses else 'neg' }}">{{ t.wins }}W / {{ t.losses }}L</td>
        <td class="{{ 'pos' if t.total_pnl >= 0 else 'neg' }}">${{ "%+.2f"|format(t.total_pnl) }}</td>
        <td class="{{ 'pos' if t.avg_pct >= 0 else 'neg' }}">{{ "%+.1f"|format(t.avg_pct) }}%</td>
        <td class="pos">+${{ "%.2f"|format(t.best) }}</td>
        <td class="neg">-${{ "%.2f"|format(t.worst) }}</td>
      </tr>
      {% endfor %}
    </table>
    {% else %}
    <div class="empty">No closed trades yet</div>
    {% endif %}
  </div>
</div>
</div><!-- end tab-analytics -->

<!-- TAB: KNOWLEDGE BASE -->
<div id="tab-knowledge" class="tab-content">
<div class="container">
  {% for cat, entries in knowledge_base.items() %}
  {% if entries %}
  <div class="row-label">{{ cat }}</div>
  <div class="section">
    {% for e in entries|reverse %}
    <div style="padding:8px 0;border-bottom:1px solid #2d3748;">
      <span style="font-size:0.65rem;color:#475569;">{{ e.ts }}</span><br>
      <span style="font-size:0.82rem;color:#e2e8f0;">{{ e.content }}</span>
    </div>
    {% endfor %}
  </div>
  {% endif %}
  {% endfor %}
</div>
</div><!-- end tab-knowledge -->

<script>
function showTab(name) {
  document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(el => el.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  event.target.classList.add('active');
}
function filterTrades() {
  const q = document.getElementById('trade-filter').value.toLowerCase();
  document.querySelectorAll('#trade-table tr').forEach((row, i) => {
    if (i === 0) return;
    row.style.display = row.cells[0].textContent.toLowerCase().includes(q) ? '' : 'none';
  });
}
</script>
</body>
</html>
"""

def _calc_streak(trades: list[dict]) -> str:
    if not trades:
        return "—"
    streak_type = "W" if float(trades[-1].get("gain_loss_usd", 0)) >= 0 else "L"
    count = 0
    for t in reversed(trades):
        is_win = float(t.get("gain_loss_usd", 0)) >= 0
        if (is_win and streak_type == "W") or (not is_win and streak_type == "L"):
            count += 1
        else:
            break
    return f"{streak_type}{count}"


def _build_analytics(closed_trades: list[dict]) -> dict:
    if not closed_trades:
        return {
            "win_rate": 0, "wins": 0, "losses": 0,
            "avg_win": 0, "avg_win_pct": 0, "avg_loss": 0, "avg_loss_pct": 0,
            "profit_factor": 0, "best_trade_usd": 0, "best_trade_token": "—",
            "worst_trade_usd": 0, "worst_trade_token": "—",
            "avg_hold_days": 0, "by_token": [], "streak": "—",
        }

    wins   = [t for t in closed_trades if float(t.get("gain_loss_usd", 0)) >= 0]
    losses = [t for t in closed_trades if float(t.get("gain_loss_usd", 0)) < 0]

    avg_win      = sum(float(t["gain_loss_usd"]) for t in wins) / len(wins) if wins else 0
    avg_win_pct  = sum(float(t["gain_loss_pct"]) for t in wins) / len(wins) if wins else 0
    avg_loss     = abs(sum(float(t["gain_loss_usd"]) for t in losses) / len(losses)) if losses else 0
    avg_loss_pct = abs(sum(float(t["gain_loss_pct"]) for t in losses) / len(losses)) if losses else 0
    gross_wins   = sum(float(t["gain_loss_usd"]) for t in wins)
    gross_losses = abs(sum(float(t["gain_loss_usd"]) for t in losses))
    profit_factor = round(gross_wins / gross_losses, 2) if gross_losses > 0 else 0

    best  = max(closed_trades, key=lambda t: float(t.get("gain_loss_usd", 0)))
    worst = min(closed_trades, key=lambda t: float(t.get("gain_loss_usd", 0)))
    avg_hold = sum(int(t.get("hold_days", 0)) for t in closed_trades) / len(closed_trades)

    # By token
    tokens = {}
    for t in closed_trades:
        tok = t.get("token", "?")
        if tok not in tokens:
            tokens[tok] = {"count": 0, "wins": 0, "losses": 0, "total_pnl": 0, "pcts": [], "usd_list": []}
        pnl = float(t.get("gain_loss_usd", 0))
        pct = float(t.get("gain_loss_pct", 0))
        tokens[tok]["count"] += 1
        tokens[tok]["total_pnl"] += pnl
        tokens[tok]["pcts"].append(pct)
        tokens[tok]["usd_list"].append(pnl)
        if pnl >= 0: tokens[tok]["wins"] += 1
        else:        tokens[tok]["losses"] += 1

    by_token = sorted([
        {
            "token":     tok,
            "count":     d["count"],
            "wins":      d["wins"],
            "losses":    d["losses"],
            "total_pnl": round(d["total_pnl"], 2),
            "avg_pct":   round(sum(d["pcts"]) / len(d["pcts"]), 1),
            "best":      round(max(d["usd_list"]), 2),
            "worst":     abs(round(min(d["usd_list"]), 2)),
        }
        for tok, d in tokens.items()
    ], key=lambda x: x["total_pnl"], reverse=True)

    return {
        "win_rate":         round(len(wins) / len(closed_trades) * 100),
        "wins":             len(wins),
        "losses":           len(losses),
        "avg_win":          round(avg_win, 2),
        "avg_win_pct":      round(avg_win_pct, 1),
        "avg_loss":         round(avg_loss, 2),
        "avg_loss_pct":     round(avg_loss_pct, 1),
        "profit_factor":    profit_factor,
        "best_trade_usd":   round(float(best.get("gain_loss_usd", 0)), 2),
        "best_trade_token": best.get("token", "—"),
        "worst_trade_usd":  abs(round(float(worst.get("gain_loss_usd", 0)), 2)),
        "worst_trade_token": worst.get("token", "—"),
        "avg_hold_days":    round(avg_hold, 1),
        "by_token":         by_token,
        "streak":           _calc_streak(closed_trades),
    }


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
@require_auth
def index():
    session = req.Session()
    session.verify = certifi.where()
    w3 = Web3(Web3.HTTPProvider(BASE_RPC_URL, session=session))

    market = Market()
    prices = market.get_all_prices()

    wallet_address = _get_wallet_address()

    balances   = _get_full_balances(w3, wallet_address, prices)
    realized   = get_realized_summary()
    closed     = _load_csv("records/realized_gains.csv")
    txns       = list(reversed(_load_csv("records/transactions.csv")))

    from bot.token_cache import list_all as list_token_cache
    tc = list_token_cache()

    # Known CoinGecko IDs for registry tokens
    KNOWN_CG_IDS = {
        "WETH":  "ethereum",
        "cbBTC": "coinbase-wrapped-btc",
        "cbETH": "coinbase-wrapped-staked-eth",
        "USDC":  "usd-coin",
        "USDT":  "tether",
        "DAI":   "dai",
        "ETH":   "ethereum",
    }

    # Build symbol → cg_id map from token cache
    sym_to_cg = dict(KNOWN_CG_IDS)
    cg_prices  = {}
    for cg_id, info in tc.items():
        stored_sym = info.get("symbol", "").upper()
        if stored_sym and stored_sym not in sym_to_cg:
            sym_to_cg[stored_sym] = cg_id
        if info.get("price", 0) > 0:
            cg_prices[stored_sym] = info["price"]

    # Fetch live prices for custom tokens via CoinGecko (IDs we know)
    custom_ids = [cg_id for sym, cg_id in sym_to_cg.items()
                  if sym not in prices and cg_id not in ("ethereum","usd-coin","tether","dai",
                                                          "coinbase-wrapped-btc","coinbase-wrapped-staked-eth")]
    if custom_ids:
        try:
            import time as _t
            _t.sleep(1)
            resp = req.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": ",".join(set(custom_ids)), "vs_currencies": "usd"},
                timeout=10, verify=certifi.where(),
            )
            if resp.status_code == 200:
                live = resp.json()
                for sym, cg_id in sym_to_cg.items():
                    if cg_id in live:
                        prices[sym] = live[cg_id]["usd"]
        except Exception:
            pass

    # Fill remaining from cache
    for sym, p in cg_prices.items():
        if sym not in prices:
            prices[sym] = p

    open_pos = get_position_summary(prices)

    # Add CoinGecko ID and URL to each open position
    for p in open_pos:
        sym   = p["symbol"].upper()
        cg_id = sym_to_cg.get(sym, "")
        p["cg_id"]  = cg_id
        p["cg_url"] = f"https://www.coingecko.com/en/coins/{cg_id}" if cg_id else ""

    # Portfolio total = USDC + ETH + all open position values
    usdc_bal  = balances.get("USDC", {}).get("balance", 0.0)
    usdc_usd  = usdc_bal
    eth_bal   = balances.get("ETH", {}).get("balance", 0.0)
    eth_price = prices.get("WETH", 0.0)
    eth_usd   = eth_bal * eth_price

    # Use current value for positions with live price, cost basis as fallback
    pos_total = 0.0
    for p in open_pos:
        if p["current_value"] > 0:
            pos_total += p["current_value"]
        elif p["cost_basis_usd"] > 0:
            pos_total += p["cost_basis_usd"]

    total_usd = usdc_usd + eth_usd + pos_total

    unrealized   = sum(p["gain_loss_usd"] for p in open_pos)
    deployed_usd = sum(p["cost_basis_usd"] for p in open_pos)
    usdc_pct     = (usdc_usd / total_usd * 100) if total_usd > 0 else 0

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

    # Cost tracking
    costs = get_cost_summary(eth_price)
    total_gas_usd = costs["gas_total_usd"] or sum(float(t.get("gas_cost_eth",0) or 0) for t in txns) * eth_price

    # Current gas price
    try:
        gas_price_gwei = round(float(Web3.from_wei(w3.eth.gas_price, "gwei")), 4)
    except Exception:
        gas_price_gwei = 0

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
        "total_gas_usd":        round(total_gas_usd, 4),
        "total_txns":           len(txns),
        "anthropic_today":      costs["anthropic_today"],
        "anthropic_total":      costs["anthropic_total"],
        "anthropic_7d":         costs["anthropic_7d"],
        "total_costs":          costs["total_costs"],
        "net_profit":           round((realized["total_realized_gain_usd"] + unrealized) - costs["total_costs"], 2),
        "capital_floor":        capital.get_floor(),
        "capital_withdrawable": capital.get_withdrawable(),
        "capital_max_deploy":   capital.get_max_deploy(total_usd),
        "gas_price_gwei":       gas_price_gwei,
    }

    analytics = _build_analytics(closed)
    kb = knowledge_module.get_all()

    return render_template_string(
        HTML,
        stats=stats,
        open_positions=open_pos,
        closed_trades=list(reversed(closed)),
        transactions=txns,
        watchlist=watchlist,
        cache_updated=cache_updated,
        bot_version=BOT_VERSION,
        analytics=analytics,
        knowledge_base=kb,
    )


@app.route("/block", methods=["POST"])
@require_auth
def block_token():
    symbol = request.form.get("symbol", "").upper()
    cg_id  = request.form.get("cg_id", "")
    # Validate symbol — only allow alphanumeric to prevent injection
    if symbol and symbol.isalnum():
        block(symbol, cg_id, reason="User blocked via dashboard")
    return redirect("/")


@app.route("/unblock", methods=["POST"])
@require_auth
def unblock_token():
    symbol = request.form.get("symbol", "").upper()
    if symbol and symbol.isalnum():
        unblock(symbol)
    return redirect("/")


@app.route("/api/summary")
@require_auth
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
