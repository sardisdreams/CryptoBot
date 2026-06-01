import os
import re

WIKI_DIR = "wiki"


def _parse_frontmatter(content: str) -> dict:
    meta = {}
    if content.startswith("---"):
        end = content.find("---", 3)
        if end != -1:
            for line in content[3:end].strip().splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    meta[k.strip()] = v.strip().strip('"')
    return meta


def load_coin(symbol: str) -> dict | None:
    path = os.path.join(WIKI_DIR, f"{symbol.upper()}.md")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        content = f.read()
    meta = _parse_frontmatter(content)
    # Strip frontmatter block for body
    body = re.sub(r"^---.*?---\n", "", content, flags=re.DOTALL).strip()
    return {"symbol": symbol, "meta": meta, "body": body, "path": path}


def get_summary(symbol: str) -> str | None:
    """Return a compact summary for the agent context (key facts only)."""
    data = load_coin(symbol)
    if not data:
        return None
    meta = data["meta"]
    body = data["body"]

    # Extract sections the agent cares most about
    sections = {}
    current = None
    for line in body.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            sections[current] = []
        elif current:
            sections[current].append(line)

    def get_section(name: str) -> str:
        lines = sections.get(name, [])
        text = "\n".join(l for l in lines if l.strip()).strip()
        return text[:500] if text else ""

    parts = [
        f"### {meta.get('name', symbol)} ({symbol})",
        f"Risk: {meta.get('risk', 'Unknown')} | Type: {meta.get('type', 'Unknown')}",
    ]

    for section in ["What it is", "Key catalysts to watch", "Trading notes", "Risk factors"]:
        content = get_section(section)
        if content:
            parts.append(f"\n**{section}:**\n{content}")

    return "\n".join(parts)


def get_all_summaries(symbols: list[str]) -> str:
    """Return formatted summaries for all available coins."""
    summaries = []
    for sym in symbols:
        s = get_summary(sym)
        if s:
            summaries.append(s)
    return "\n\n---\n\n".join(summaries)


def get_watchlist() -> list[dict]:
    """Return all coins marked as WATCHLIST status."""
    watchlist = []
    if not os.path.exists(WIKI_DIR):
        return []
    for fname in os.listdir(WIKI_DIR):
        if not fname.endswith(".md") or fname == "index.md":
            continue
        with open(os.path.join(WIKI_DIR, fname)) as f:
            content = f.read()
        meta = _parse_frontmatter(content)
        if "WATCHLIST" in meta.get("status", ""):
            watchlist.append(meta)
    return watchlist
