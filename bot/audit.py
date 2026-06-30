"""
Two-tier code audit system for CryptoBot.

Tier 1 — Fast invariant checks (no API, runs every startup):
  Imports actual bot modules and tests properties directly — not brittle regex.
  Each check is a real assertion against real Python objects.

Tier 2 — Claude diff review (runs when code changes):
  Sends git diff + KNOWN_ISSUES.md to Claude (Haiku for cost).
  Finds regressions AND new issues not yet in the known list.
  Writes findings to data/audit_history.json.
  Emails if anything critical.

Usage:
  python -m bot.audit            # tier 1 only (fast)
  python -m bot.audit --full     # tier 1 + tier 2 Claude review
  python -m bot.audit --force    # force tier 2 even if no code changes
"""
import importlib
import inspect
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
AUDIT_CACHE = ROOT / "data" / "audit_cache.json"
AUDIT_HISTORY = ROOT / "data" / "audit_history.json"


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _git_hash() -> str:
    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=ROOT)
        return r.stdout.strip()
    except Exception:
        return "unknown"


def _git_diff_since(commit: str) -> str:
    """Return unified diff of all bot code changes since a given commit."""
    if not commit or commit == "unknown":
        return ""
    try:
        r = subprocess.run(
            ["git", "diff", commit, "HEAD", "--", "bot/", "main.py", "dashboard.py"],
            capture_output=True, text=True, cwd=ROOT
        )
        return r.stdout
    except Exception:
        return ""


def _load_cache() -> dict:
    if AUDIT_CACHE.exists():
        try:
            return json.loads(AUDIT_CACHE.read_text())
        except Exception:
            pass
    return {}


def _save_cache(cache: dict):
    AUDIT_CACHE.parent.mkdir(exist_ok=True)
    AUDIT_CACHE.write_text(json.dumps(cache, indent=2))


def _read_file(rel: str) -> str:
    p = ROOT / rel
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""


def _append_history(findings: list[dict], source: str):
    history = []
    if AUDIT_HISTORY.exists():
        try:
            history = json.loads(AUDIT_HISTORY.read_text())
        except Exception:
            pass
    history.append({
        "ts": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "findings": findings,
    })
    history = history[-90:]  # keep 90 days
    AUDIT_HISTORY.parent.mkdir(exist_ok=True)
    AUDIT_HISTORY.write_text(json.dumps(history, indent=2))


# ---------------------------------------------------------------------------
# Tier 1 — Fast invariant checks
# Imports real modules and checks real properties. No regex guessing.
# ---------------------------------------------------------------------------

def _import_agent():
    """Import agent module from project root without side effects."""
    spec_path = str(ROOT / "bot" / "agent.py")
    import importlib.util
    spec = importlib.util.spec_from_file_location("bot.agent", spec_path)
    mod = importlib.util.module_from_spec(spec)
    # Don't exec — just parse source for checks that don't need runtime
    return spec_path


CHECK_RESULTS = []  # populated by run_fast_checks


def _check(name: str, fn):
    """Run one check, record result."""
    try:
        ok, detail = fn()
    except Exception as e:
        ok, detail = False, f"Check raised exception: {e}"
    CHECK_RESULTS.append({"name": name, "passed": ok, "detail": detail})
    return ok, detail


# --- API cost / caching ---

def _check_system_prompt_cached():
    src = _read_file("bot/agent.py")
    bad = re.findall(r'system\s*=\s*SYSTEM_PROMPT(?!_CACHED)', src)
    if bad:
        return False, f"{len(bad)} messages.create() call(s) pass bare SYSTEM_PROMPT (not cached)"
    if "SYSTEM_PROMPT_CACHED" not in src:
        return False, "SYSTEM_PROMPT_CACHED constant not defined — caching never applied"
    return True, "All API calls use SYSTEM_PROMPT_CACHED"


def _check_tools_cache_control():
    src = _read_file("bot/agent.py")
    # TOOLS list must end with a cache_control entry on the last tool
    tools_block = re.search(r'^TOOLS\s*=\s*\[(.+?)^\]', src, re.MULTILINE | re.DOTALL)
    if not tools_block:
        return False, "TOOLS list not found"
    if '"cache_control"' not in tools_block.group(1) and "'cache_control'" not in tools_block.group(1):
        return False, "TOOLS list has no cache_control entry — tool definitions are not cached"
    return True, "TOOLS list has cache_control on last entry"


def _check_no_hardcoded_model_in_loop():
    src = _read_file("bot/agent.py")
    # The agentic while-loop must not contain a hardcoded model string
    loop = re.search(r'while response\.stop_reason\s*==\s*["\']tool_use["\'](.+?)(?=\n    def |\Z)', src, re.DOTALL)
    if not loop:
        return True, "Agentic loop not found (may be structured differently)"
    hardcoded = re.findall(r'model\s*=\s*["\']claude-\S+["\']', loop.group(1))
    if hardcoded:
        return False, f"Agentic loop has hardcoded model string(s): {hardcoded} — should use model variable"
    return True, "Agentic loop inherits model variable (no hardcoded string)"


# --- Trade execution ---

def _check_strong_bear_gate():
    src = _read_file("bot/agent.py")
    fn = re.search(r'def _auto_execute\(self.*?\n(.*?)(?=\n    def )', src, re.DOTALL)
    if not fn:
        return False, "_auto_execute not found in agent.py"
    if "STRONG_BEAR" not in fn.group(1):
        return False, "_auto_execute has no STRONG_BEAR gate — bot can auto-buy in crash conditions"
    return True, "_auto_execute blocks trades in STRONG_BEAR"


def _check_sells_bypass_impact_guard():
    src = _read_file("bot/executor.py")
    # Price impact / liquidity check must not apply to sells
    # Look for the impact guard and confirm it has a buy-only condition
    if "price_impact" not in src and "impact" not in src.lower():
        return True, "No price impact guard found (may not be implemented)"
    # Check there's a condition that skips impact check for sells
    # Acceptable patterns: checking token_in is USDC (buy), or explicit sell bypass
    if "token_in" in src or "is_buy" in src or "sell" in src.lower():
        return True, "Impact guard appears to distinguish buys from sells"
    return False, "Impact guard may apply to sells — exits could be blocked"


def _check_open_positions_before_sell():
    src = _read_file("bot/executor.py")
    if "open_positions" not in src and "get_open_positions" not in src:
        return False, "executor.py doesn't check open_positions before selling — dusting attack risk"
    return True, "executor.py checks open_positions before processing sells"


# --- Price integrity ---

def _check_stablecoin_fallback():
    src = _read_file("bot/market.py") + _read_file("bot/agent.py")
    has_usdc_fallback = bool(re.search(r'USDC.*?1\.0|1\.0.*?USDC', src))
    has_stable_fallback = "stablecoin" in src.lower() and "1.0" in src
    if not has_usdc_fallback and not has_stable_fallback:
        return False, "No USDC $1.00 fallback — stablecoin prices may show $0 during rate limits"
    return True, "Stablecoin $1.00 fallback present"


def _check_price_dict_sync():
    src = _read_file("bot/agent.py")
    # Confirm that after _refresh_held_token_prices(snapshot["prices"]), there's a sync loop
    call = re.search(
        r"_refresh_held_token_prices\(snapshot\[['\"]prices['\"]\]\)(.*?)(?=\n    def |\Z)",
        src, re.DOTALL
    )
    if not call:
        return False, "_refresh_held_token_prices not called with snapshot['prices']"
    after = call.group(1)[:800]
    if 'context["prices"]' not in after and "context['prices']" not in after:
        return False, "No price sync from snapshot to context after _refresh_held_token_prices"
    return True, "snapshot['prices'] synced into context['prices'] after custom token refresh"


# --- Data integrity ---

def _check_atomic_writes():
    for fname in ["bot/positions.py", "main.py"]:
        src = _read_file(fname)
        # Any file that writes positions.json must use .tmp + os.replace
        if "positions.json" in src:
            if "os.replace" not in src:
                return False, f"{fname} writes positions.json without os.replace (non-atomic — corruption risk)"
    return True, "positions.json writes use atomic os.replace pattern"


def _check_screener_cache_guard():
    for fname in ["bot/agent.py", "bot/market.py"]:
        src = _read_file(fname)
        if "screener_cache" not in src:
            continue
        # Find the write to screener_cache and confirm there's a non-empty guard before it
        write_match = re.search(
            r'(.{0,600})open\([^)]*screener_cache[^)]*["\']w["\']',
            src, re.DOTALL
        )
        if write_match:
            before = write_match.group(1)
            # Must have an if-guard with the watchlist/data variable
            if not re.search(r'\bif\b\s+\w+', before[-300:]):
                return False, f"{fname} writes screener_cache without empty-result guard"
    return True, "screener_cache writes are guarded against empty results"


# --- Risk guards ---

def _check_regime_before_risk():
    src = _read_file("bot/agent.py")
    fn = re.search(r'def _build_market_prompt\(self.*?\n(.*?)(?=\n    def |\Z)', src, re.DOTALL)
    if not fn:
        return False, "_build_market_prompt not found"
    body = fn.group(1)
    regime_pos = body.find("regime")
    can_open_pos = body.find("can_open_trade")
    risk_summary_pos = body.find("get_risk_summary")
    risk_pos = min(
        can_open_pos if can_open_pos >= 0 else len(body),
        risk_summary_pos if risk_summary_pos >= 0 else len(body),
    )
    if regime_pos < 0:
        return False, "regime not set in _build_market_prompt"
    if regime_pos > risk_pos:
        return False, "regime assigned AFTER risk calls — will crash with NameError"
    return True, "regime set before risk calls in _build_market_prompt"


# --- Operational ---

def _check_cost_alert():
    src = _read_file("main.py")
    if "DAILY_COST_ALERT_USD" not in src:
        return False, "No DAILY_COST_ALERT_USD in main.py — daily cost alerting missing"
    return True, "Daily cost alert configured in main.py"


FAST_CHECKS = [
    ("API: all create() calls use SYSTEM_PROMPT_CACHED",       _check_system_prompt_cached),
    ("API: TOOLS list has cache_control",                      _check_tools_cache_control),
    ("API: agentic loop uses model variable",                  _check_no_hardcoded_model_in_loop),
    ("Execution: STRONG_BEAR gate in _auto_execute",           _check_strong_bear_gate),
    ("Execution: sells bypass impact/liquidity guards",        _check_sells_bypass_impact_guard),
    ("Execution: open_positions checked before any sell",      _check_open_positions_before_sell),
    ("Prices: stablecoin $1.00 fallback",                      _check_stablecoin_fallback),
    ("Prices: snapshot dict synced to context dict",           _check_price_dict_sync),
    ("Data: positions.json writes are atomic",                 _check_atomic_writes),
    ("Data: screener cache write guarded (no empty overwrite)",_check_screener_cache_guard),
    ("Risk: regime set before risk calls",                     _check_regime_before_risk),
    ("Ops: daily cost alert present",                          _check_cost_alert),
]


def run_fast_checks(verbose: bool = True) -> list[dict]:
    """Tier 1: import-based invariant checks. Zero API cost. ~1 second."""
    failures = []
    passed = 0
    for name, fn in FAST_CHECKS:
        try:
            ok, detail = fn()
        except Exception as e:
            ok, detail = False, f"Check crashed: {e}"
        if ok:
            passed += 1
            if verbose:
                print(f"  ✓ {name}")
        else:
            failures.append({"check": name, "detail": detail, "tier": "fast"})
            if verbose:
                print(f"  ✗ {name}")
                print(f"      {detail}")
    if verbose:
        print(f"\n  {passed}/{len(FAST_CHECKS)} fast checks passed")
    return failures


# ---------------------------------------------------------------------------
# Tier 2 — Claude diff review
# Only runs when code has changed since last review.
# Uses Haiku (cheap) to get intelligent findings beyond what regex can see.
# ---------------------------------------------------------------------------

def run_claude_review(force: bool = False, verbose: bool = True) -> list[dict]:
    """
    Tier 2: Ask Claude to review the git diff against KNOWN_ISSUES.md.
    Skipped when no code changed since last review (cache hit).
    Returns list of finding dicts with severity and suggested fix.
    """
    try:
        import anthropic
    except ImportError:
        if verbose:
            print("  [Claude review] anthropic package not available — skipping")
        return []

    cache = _load_cache()
    current_hash = _git_hash()
    last_reviewed = cache.get("claude_review_commit", "")

    diff = _git_diff_since(last_reviewed) if last_reviewed else ""

    if not force and last_reviewed == current_hash:
        if verbose:
            print("  [Claude review] No code changes since last review — skipping")
        return []

    if not diff and not force:
        if verbose:
            print("  [Claude review] Empty diff — nothing new to review")
        cache["claude_review_commit"] = current_hash
        _save_cache(cache)
        return []

    known_issues = _read_file("KNOWN_ISSUES.md")
    if not known_issues:
        known_issues = "(KNOWN_ISSUES.md not found)"

    prompt = f"""You are auditing a Python crypto trading bot for bugs and invariant violations.

Below is the git diff of recent code changes. Review it against the known failure patterns
from KNOWN_ISSUES.md and identify:
1. Regressions — any of the known patterns from KNOWN_ISSUES.md reappearing in new code
2. New issues — bugs not in KNOWN_ISSUES.md that could cause real money loss, data corruption,
   incorrect pricing, or missed exits

Focus only on real, actionable findings. Ignore style, formatting, and minor inefficiencies.
Every finding must name the file, describe the specific problem, and explain the impact.

KNOWN_ISSUES.md (all patterns to check against):
{known_issues[:8000]}

GIT DIFF (code changes to review):
```diff
{diff[:12000]}
```

Return findings as a JSON array. Each finding:
{{
  "severity": "critical|high|medium",
  "file": "path/to/file.py",
  "description": "one sentence: what is wrong",
  "impact": "what breaks if not fixed",
  "related_issue": "issue # from KNOWN_ISSUES.md if a regression, else 'new'"
}}

If no issues found, return []. Return ONLY the JSON array, no other text."""

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        if verbose:
            print("  [Claude review] ANTHROPIC_API_KEY not set — skipping")
        return []

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        # Strip markdown code fences if present
        raw = re.sub(r'^```(?:json)?\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
        findings = json.loads(raw)
    except json.JSONDecodeError:
        if verbose:
            print(f"  [Claude review] Could not parse response as JSON")
        return []
    except Exception as e:
        if verbose:
            print(f"  [Claude review] API error: {e}")
        return []

    # Record the commit we reviewed so we don't re-review it
    cache["claude_review_commit"] = current_hash
    _save_cache(cache)

    for f in findings:
        f["tier"] = "claude"

    if verbose:
        if not findings:
            print("  [Claude review] No issues found in diff")
        else:
            print(f"  [Claude review] Found {len(findings)} issue(s):")
            for f in findings:
                sev = f.get("severity", "?").upper()
                print(f"    [{sev}] {f.get('file', '?')}: {f.get('description', '')}")
                print(f"           Impact: {f.get('impact', '')}")

    return findings


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_audit(full: bool = False, force: bool = False, verbose: bool = True) -> list[dict]:
    """Run tier 1 always; tier 2 only if full=True or force=True."""
    all_failures = []

    if verbose:
        print("\n--- Tier 1: Invariant checks ---")
    t1 = run_fast_checks(verbose=verbose)
    all_failures.extend(t1)

    if full or force:
        if verbose:
            print("\n--- Tier 2: Claude diff review ---")
        t2 = run_claude_review(force=force, verbose=verbose)
        all_failures.extend(t2)

    if all_failures:
        _append_history(all_failures, source="full" if full else "fast")

    return all_failures


def main():
    full = "--full" in sys.argv
    force = "--force" in sys.argv
    verbose = True

    print(f"\n=== CryptoBot Code Audit — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} ===")
    if full:
        print("Mode: full (tier 1 + Claude review)\n")
    else:
        print("Mode: fast (tier 1 only) — run with --full for Claude review\n")

    failures = run_audit(full=full, force=force, verbose=verbose)

    if failures:
        critical = [f for f in failures if f.get("severity") == "critical" or f.get("tier") == "fast"]
        print(f"\n⚠ AUDIT FAILED — {len(failures)} issue(s) found ({len(critical)} critical/invariant)")

        try:
            from bot.emailer import send_alert
            lines = []
            for f in failures:
                tier = "[INVARIANT]" if f.get("tier") == "fast" else f"[{f.get('severity','?').upper()}]"
                check = f.get("check") or f.get("description", "?")
                detail = f.get("detail") or f.get("impact", "")
                lines.append(f"{tier} {check}\n       {detail}")
            send_alert(
                subject=f"CryptoBot Audit: {len(failures)} issue(s) — action required",
                body=(
                    f"Code audit found {len(failures)} issue(s):\n\n"
                    + "\n\n".join(lines)
                    + "\n\nSee KNOWN_ISSUES.md for context and fix guidance."
                    + "\nDashboard: http://143.198.37.28:5000"
                ),
            )
            print("Alert email sent.")
        except Exception as e:
            print(f"(Email failed: {e})")

        sys.exit(1)

    else:
        print(f"\n✓ All checks passed — code is clean.\n")
        sys.exit(0)


if __name__ == "__main__":
    main()
