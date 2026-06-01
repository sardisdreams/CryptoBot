import time
import requests
import certifi
from bot.logger import setup_logger

logger = setup_logger("evaluator")

SSL = certifi.where()

COINGECKO_COIN = "https://api.coingecko.com/api/v3/coins/{}"


def score_coin(cg_id: str, basescan_api_key: str = None) -> dict:
    """
    Score a coin on safety criteria. Returns a score 0–100 and a breakdown.
    Higher score = safer to trade.
    """
    score = 0
    flags = []
    details = {}

    try:
        time.sleep(1.5)  # respect CoinGecko free tier rate limit
        resp = requests.get(
            COINGECKO_COIN.format(cg_id),
            params={"localization": "false", "tickers": "false", "community_data": "true"},
            timeout=15,
            verify=SSL,
        )
        if resp.status_code == 429:
            return {"score": -1, "grade": "?", "flags": ["CoinGecko rate limited — try again next tick"], "details": {}}
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return {"score": -1, "grade": "?", "flags": [f"Could not fetch data: {e}"], "details": {}}

    # --- Market cap check (10 pts) ---
    mc = data.get("market_data", {}).get("market_cap", {}).get("usd", 0) or 0
    details["market_cap_usd"] = mc
    if mc >= 50_000_000:
        score += 10
    elif mc >= 10_000_000:
        score += 6
    elif mc >= 5_000_000:
        score += 3
    else:
        flags.append("Market cap under $5M — very easy to manipulate")

    # --- Liquidity / volume (15 pts) ---
    vol = data.get("market_data", {}).get("total_volume", {}).get("usd", 0) or 0
    details["volume_24h_usd"] = vol
    vol_mc_ratio = vol / mc if mc > 0 else 0
    if vol >= 1_000_000:
        score += 15
    elif vol >= 200_000:
        score += 10
    elif vol >= 50_000:
        score += 5
    else:
        flags.append("Very low 24h volume — liquidity risk, hard to exit")

    # --- Age (15 pts) ---
    genesis = data.get("genesis_date")
    details["genesis_date"] = genesis
    if genesis:
        from datetime import date
        try:
            days_old = (date.today() - date.fromisoformat(genesis)).days
            details["days_old"] = days_old
            if days_old >= 365:
                score += 15
            elif days_old >= 180:
                score += 10
            elif days_old >= 90:
                score += 5
            else:
                flags.append(f"Token only {days_old} days old — insufficient track record")
        except Exception:
            pass
    else:
        flags.append("No genesis date available — age unknown")

    # --- Website (10 pts) ---
    links = data.get("links", {})
    homepage = [h for h in links.get("homepage", []) if h]
    details["website"] = homepage[0] if homepage else None
    if homepage:
        score += 10
    else:
        flags.append("No website listed")

    # --- Twitter presence (10 pts) ---
    twitter = links.get("twitter_screen_name", "")
    details["twitter"] = twitter
    twitter_followers = data.get("community_data", {}).get("twitter_followers", 0) or 0
    details["twitter_followers"] = twitter_followers
    if twitter and twitter_followers >= 10_000:
        score += 10
    elif twitter and twitter_followers >= 1_000:
        score += 6
    elif twitter:
        score += 3
        flags.append(f"Twitter exists but low followers ({twitter_followers:,})")
    else:
        flags.append("No Twitter account listed")

    # --- GitHub activity (10 pts) ---
    repos = links.get("repos_url", {}).get("github", [])
    details["github"] = repos[0] if repos else None
    dev_data = data.get("developer_data", {})
    commits_4w = dev_data.get("commit_count_4_weeks", 0) or 0
    details["github_commits_4w"] = commits_4w
    if repos and commits_4w >= 10:
        score += 10
    elif repos and commits_4w > 0:
        score += 5
    elif repos:
        score += 2
        flags.append("GitHub exists but no recent commits — inactive development?")
    else:
        flags.append("No GitHub repository")

    # --- Price stability / volatility check (10 pts) ---
    change_7d = data.get("market_data", {}).get("price_change_percentage_7d", 0) or 0
    change_30d = data.get("market_data", {}).get("price_change_percentage_30d", 0) or 0
    details["change_7d_pct"] = change_7d
    details["change_30d_pct"] = change_30d
    if abs(change_7d) < 30:
        score += 10
    elif abs(change_7d) < 60:
        score += 5
    else:
        flags.append(f"Extreme 7d volatility ({change_7d:+.1f}%) — possible pump or dump in progress")

    # --- Volume/market cap ratio (pump detection) (10 pts) ---
    if vol_mc_ratio > 5.0:
        flags.append(f"Volume/market cap ratio very high ({vol_mc_ratio:.1f}x) — possible pump and dump")
        score += 0
    elif vol_mc_ratio > 1.0:
        flags.append(f"Volume/market cap ratio elevated ({vol_mc_ratio:.1f}x) — monitor closely")
        score += 5
    else:
        score += 10

    # --- CoinGecko score (10 pts) ---
    cg_score = data.get("coingecko_score", 0) or 0
    details["coingecko_score"] = cg_score
    if cg_score >= 50:
        score += 10
    elif cg_score >= 25:
        score += 5

    # Grade
    if score >= 80:
        grade = "A"
    elif score >= 65:
        grade = "B"
    elif score >= 50:
        grade = "C"
    elif score >= 35:
        grade = "D"
    else:
        grade = "F"

    result = {
        "cg_id":   cg_id,
        "score":   score,
        "grade":   grade,
        "flags":   flags,
        "details": details,
    }
    logger.info(f"Evaluated {cg_id}: {grade} ({score}/100) — {len(flags)} flags")
    return result


def format_report(evaluation: dict) -> str:
    lines = [
        f"**Risk Score: {evaluation['score']}/100 (Grade: {evaluation['grade']})**",
        "",
        "Details:",
    ]
    d = evaluation["details"]
    if d.get("market_cap_usd"):
        lines.append(f"  Market cap: ${d['market_cap_usd']:,.0f}")
    if d.get("volume_24h_usd"):
        lines.append(f"  24h volume: ${d['volume_24h_usd']:,.0f}")
    if d.get("days_old"):
        lines.append(f"  Age: {d['days_old']} days")
    if d.get("twitter"):
        lines.append(f"  Twitter: @{d['twitter']} ({d.get('twitter_followers', 0):,} followers)")
    if d.get("website"):
        lines.append(f"  Website: {d['website']}")
    if d.get("github_commits_4w") is not None:
        lines.append(f"  GitHub commits (4w): {d['github_commits_4w']}")

    if evaluation["flags"]:
        lines += ["", "FLAGS:"]
        for flag in evaluation["flags"]:
            lines.append(f"  - {flag}")

    return "\n".join(lines)
