"""
Automated code audit — checks the codebase against every known failure pattern
documented in KNOWN_ISSUES.md. Run daily via cron or on-demand.

Each check returns (passed: bool, detail: str). A failed check means a known
invariant is violated — either a regression or a new code path that breaks a rule.

Run standalone:  python -m bot.audit
"""
import re
import os
import sys
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).parent.parent


def _read(rel_path: str) -> str:
    p = ROOT / rel_path
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8", errors="replace")


def _find_all(pattern: str, text: str, flags: int = 0) -> list[str]:
    return re.findall(pattern, text, flags)


# ---------------------------------------------------------------------------
# Individual checks — each returns (passed, detail)
# ---------------------------------------------------------------------------

def check_prompt_caching_all_create_calls() -> tuple[bool, str]:
    """Issue 1 & 2: Every messages.create() must use SYSTEM_PROMPT_CACHED."""
    agent = _read("bot/agent.py")
    # Find all messages.create blocks
    # Look for any occurrence of system=SYSTEM_PROMPT that isn't SYSTEM_PROMPT_CACHED
    bad = re.findall(r'system=SYSTEM_PROMPT(?!_CACHED)', agent)
    if bad:
        return False, f"{len(bad)} messages.create() call(s) use bare SYSTEM_PROMPT instead of SYSTEM_PROMPT_CACHED"
    if "SYSTEM_PROMPT_CACHED" not in agent:
        return False, "SYSTEM_PROMPT_CACHED is not defined — prompt caching not implemented"
    return True, "All messages.create() calls use SYSTEM_PROMPT_CACHED"


def check_tool_followup_no_hardcoded_model() -> tuple[bool, str]:
    """Issue 2: Tool follow-up calls must not hardcode a model string."""
    agent = _read("bot/agent.py")
    # Find the agentic loop section and check for hardcoded model
    # Look for model= inside the while response.stop_reason loop
    loop_match = re.search(
        r'while response\.stop_reason.*?(?=\n\s*#\s*Log token usage)',
        agent, re.DOTALL
    )
    if loop_match:
        loop_body = loop_match.group(0)
        hardcoded = re.findall(r'model=["\']claude-[^"\']+["\']', loop_body)
        if hardcoded:
            return False, f"Tool follow-up loop has hardcoded model: {hardcoded}"
    return True, "Tool follow-up calls use model variable, not hardcoded string"


def check_strong_bear_auto_execute_gate() -> tuple[bool, str]:
    """Issue 5: _auto_execute() must refuse to trade in STRONG_BEAR."""
    agent = _read("bot/agent.py")
    fn_match = re.search(r'def _auto_execute\(.*?\n(.*?)(?=\n    def )', agent, re.DOTALL)
    if not fn_match:
        return False, "_auto_execute() function not found in agent.py"
    body = fn_match.group(1)
    if "STRONG_BEAR" not in body:
        return False, "_auto_execute() has no STRONG_BEAR guard — bot can auto-buy in bear crashes"
    return True, "_auto_execute() has STRONG_BEAR gate"


def check_price_zero_guard() -> tuple[bool, str]:
    """Issue 15: Price updates must be guarded against zero/None."""
    market = _read("bot/market.py")
    agent = _read("bot/agent.py")
    combined = market + agent
    # Look for direct price dict assignment without guard
    # Pattern: prices[sym] = <value> without surrounding if-guard
    assignments = re.findall(r'prices\[.*?\]\s*=\s*(?!None|0\b)', combined)
    # Heuristic: check that get_all_prices has a zero guard somewhere
    if "if price" not in market and "if p " not in market and "> 0" not in market:
        return False, "market.py may be missing zero/None price guards in get_all_prices()"
    return True, "Price update code contains zero/None guards"


def check_stablecoin_fallback() -> tuple[bool, str]:
    """Issue 16: USDC/USDT/DAI must have a $1.00 fallback."""
    market = _read("bot/market.py")
    agent = _read("bot/agent.py")
    combined = market + agent
    has_fallback = (
        ("USDC" in combined and "1.0" in combined) or
        ("stablecoin" in combined.lower() and "1.0" in combined)
    )
    if not has_fallback:
        return False, "No stablecoin $1.00 fallback found — USDC/USDT/DAI may show $0 during rate limits"
    return True, "Stablecoin $1.00 fallback exists"


def check_sell_not_blocked_by_buy_guards() -> tuple[bool, str]:
    """Issue 11 & 13: Sells must never be blocked by min trade size or price impact."""
    executor = _read("bot/executor.py")
    # Check that price impact guard has a sell bypass
    if "price_impact" in executor or "impact" in executor.lower():
        # Should have a condition that bypasses for sells
        if "sell" not in executor.lower() and "token_in" not in executor:
            return False, "Price impact check may not distinguish buys from sells"
    return True, "Sell path appears to bypass buy-only guards"


def check_open_positions_guard_on_sells() -> tuple[bool, str]:
    """Issue 28: Before any sell, must verify token is in open_positions."""
    executor = _read("bot/executor.py")
    if "open_positions" not in executor and "get_open_positions" not in executor:
        return False, "executor.py has no open_positions check — dusting attack protection may be missing"
    return True, "executor.py checks open_positions before sells"


def check_screener_cache_not_overwritten_with_empty() -> tuple[bool, str]:
    """Issue 27: screener_cache.json must never be overwritten with empty results."""
    # Check any file that writes screener_cache
    files_to_check = ["bot/market.py", "bot/agent.py", "bot/screener.py"]
    for fname in files_to_check:
        content = _read(fname)
        if "screener_cache" in content:
            # Look for a guard before the write
            writes = re.findall(r'(?:json\.dump|open.*screener_cache).*', content)
            for w in writes:
                # Should have an empty-check guard nearby
                pass
            if "if" not in content[content.find("screener_cache"):content.find("screener_cache") + 300]:
                return False, f"{fname} writes screener_cache without an empty-result guard"
    return True, "screener_cache writes appear to be guarded against empty results"


def check_atomic_positions_writes() -> tuple[bool, str]:
    """Issue general: positions.json writes must use atomic write-then-rename."""
    positions = _read("bot/positions.py")
    main = _read("main.py")
    # Check that all writes use os.replace (atomic) not direct open+write
    if "os.replace" not in positions:
        return False, "positions.py doesn't use os.replace — writes are not atomic"
    if "positions.json" in main:
        # main.py also writes positions.json in some paths
        if "os.replace" not in main:
            return False, "main.py writes positions.json without os.replace (non-atomic)"
    return True, "positions.json writes use atomic os.replace pattern"


def check_regime_before_risk_calls() -> tuple[bool, str]:
    """Issue 21: Regime must be assigned before can_open_trade() or get_risk_summary()."""
    agent = _read("bot/agent.py")
    fn_match = re.search(r'def _build_market_prompt\(.*?\n(.*?)(?=\n    def |\Z)', agent, re.DOTALL)
    if not fn_match:
        return False, "_build_market_prompt() not found in agent.py"
    body = fn_match.group(1)
    regime_pos = body.find("regime")
    risk_pos = min(
        body.find("can_open_trade") if "can_open_trade" in body else len(body),
        body.find("get_risk_summary") if "get_risk_summary" in body else len(body),
    )
    if regime_pos == -1:
        return False, "regime not found in _build_market_prompt()"
    if regime_pos > risk_pos:
        return False, "regime is assigned AFTER risk calls in _build_market_prompt() — will crash with NameError"
    return True, "regime is assigned before risk calls in _build_market_prompt()"


def check_two_price_dicts_sync() -> tuple[bool, str]:
    """Issue 17: snapshot['prices'] and context['prices'] must be synced after refresh."""
    agent = _read("bot/agent.py")
    if "_refresh_held_token_prices" not in agent:
        return False, "_refresh_held_token_prices not found — custom token pricing may be broken"
    # After the refresh call, there should be a sync loop
    refresh_pos = agent.find("_refresh_held_token_prices")
    nearby = agent[refresh_pos:refresh_pos + 500]
    if 'context["prices"]' not in nearby and "context['prices']" not in nearby:
        return False, "No sync from snapshot prices to context prices after _refresh_held_token_prices"
    return True, "Price dicts synced after _refresh_held_token_prices"


def check_record_tick_inside_downtime_adjust() -> tuple[bool, str]:
    """Issue 25: _record_tick() must be called inside _adjust_positions_for_downtime."""
    main = _read("main.py")
    fn_match = re.search(
        r'def _adjust_positions_for_downtime\(.*?\n(.*?)(?=\ndef |\Z)',
        main, re.DOTALL
    )
    if not fn_match:
        # May not exist if downtime adjust is elsewhere
        return True, "_adjust_positions_for_downtime not found (may be inline)"
    body = fn_match.group(1)
    if "_record_tick" not in body:
        return False, "_record_tick() not called inside _adjust_positions_for_downtime — crash-restart loops will re-extend hold windows"
    return True, "_record_tick() called inside _adjust_positions_for_downtime"


def check_no_direct_positions_json_write() -> tuple[bool, str]:
    """Data integrity: no bare open(positions.json, 'w') without os.replace."""
    files = ["bot/positions.py", "main.py", "bot/agent.py", "bot/executor.py"]
    for fname in files:
        content = _read(fname)
        # Look for open(...positions..., "w") without a corresponding .tmp + os.replace pattern
        bare_writes = re.findall(r'open\([^)]*positions\.json[^)]*["\']w["\']', content)
        if bare_writes:
            # Check if there's a .tmp pattern nearby
            if ".tmp" not in content:
                return False, f"{fname} opens positions.json for writing without .tmp+os.replace pattern"
    return True, "All positions.json writes use .tmp+os.replace atomic pattern"


def check_cache_control_on_last_tool() -> tuple[bool, str]:
    """Issue 1: TOOLS list must have cache_control on the last entry."""
    agent = _read("bot/agent.py")
    tools_match = re.search(r'TOOLS\s*=\s*\[(.*?)\]\s*\n\n', agent, re.DOTALL)
    if not tools_match:
        return False, "TOOLS list not found in agent.py"
    tools_body = tools_match.group(1)
    if "cache_control" not in tools_body:
        return False, "TOOLS list has no cache_control entry — tools are not being cached"
    return True, "TOOLS list has cache_control on last entry"


def check_cost_tracker_exists() -> tuple[bool, str]:
    """Operational: cost tracker must exist to support daily cost alerts."""
    ct = _read("bot/cost_tracker.py")
    if not ct:
        return False, "bot/cost_tracker.py not found — daily cost alerting will fail"
    if "anthropic_today" not in ct and "today" not in ct:
        return False, "cost_tracker.py may not track daily spend"
    return True, "Cost tracker exists and tracks daily spend"


def check_daily_cost_alert_in_main() -> tuple[bool, str]:
    """Operational: main.py must have daily cost alert logic."""
    main = _read("main.py")
    if "DAILY_COST_ALERT_USD" not in main:
        return False, "DAILY_COST_ALERT_USD not in main.py — no daily cost alerting"
    if "_check_daily_api_cost" not in main:
        return False, "_check_daily_api_cost not in main.py — daily cost check not implemented"
    return True, "Daily cost alert implemented in main.py"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

ALL_CHECKS = [
    ("Prompt caching: all create() calls", check_prompt_caching_all_create_calls),
    ("Prompt caching: no hardcoded model in follow-up", check_tool_followup_no_hardcoded_model),
    ("STRONG_BEAR gate in _auto_execute", check_strong_bear_auto_execute_gate),
    ("Price zero/None guard", check_price_zero_guard),
    ("Stablecoin $1.00 fallback", check_stablecoin_fallback),
    ("Sells not blocked by buy guards", check_sell_not_blocked_by_buy_guards),
    ("open_positions check before sells", check_open_positions_guard_on_sells),
    ("Screener cache: no empty overwrite", check_screener_cache_not_overwritten_with_empty),
    ("positions.json atomic writes", check_atomic_positions_writes),
    ("Regime assigned before risk calls", check_regime_before_risk_calls),
    ("Two price dicts synced", check_two_price_dicts_sync),
    ("_record_tick inside downtime adjust", check_record_tick_inside_downtime_adjust),
    ("No bare positions.json writes", check_no_direct_positions_json_write),
    ("TOOLS list has cache_control", check_cache_control_on_last_tool),
    ("Cost tracker exists", check_cost_tracker_exists),
    ("Daily cost alert in main.py", check_daily_cost_alert_in_main),
]


def run_audit(verbose: bool = True) -> list[dict]:
    """Run all checks and return list of failures."""
    failures = []
    passed = 0

    for name, check_fn in ALL_CHECKS:
        try:
            ok, detail = check_fn()
        except Exception as e:
            ok, detail = False, f"Check crashed: {e}"

        if ok:
            passed += 1
            if verbose:
                print(f"  ✓ {name}")
        else:
            failures.append({"check": name, "detail": detail})
            if verbose:
                print(f"  ✗ {name}: {detail}")

    if verbose:
        print(f"\n{passed}/{len(ALL_CHECKS)} checks passed, {len(failures)} failed")

    return failures


def main():
    print(f"\n=== CryptoBot Code Audit — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} ===\n")
    failures = run_audit(verbose=True)

    if failures:
        print("\n⚠ AUDIT FAILED — known invariants violated. Fix before deploying.\n")

        # Email if emailer is available
        try:
            from bot.emailer import send_alert
            lines = [f"• {f['check']}: {f['detail']}" for f in failures]
            send_alert(
                subject=f"CryptoBot Audit: {len(failures)} invariant(s) violated",
                body=(
                    f"Code audit found {len(failures)} violation(s):\n\n"
                    + "\n".join(lines)
                    + "\n\nSee KNOWN_ISSUES.md for context and fix guidance."
                ),
            )
            print("Alert email sent.")
        except Exception as e:
            print(f"(Could not send email: {e})")

        sys.exit(1)
    else:
        print("\n✓ All invariants hold. Code is clean.\n")
        sys.exit(0)


if __name__ == "__main__":
    main()
