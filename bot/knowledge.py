"""
Persistent knowledge base — the bot's source of truth.
Stores observations, token notes, market patterns, and strategy learnings
that persist across runs. The bot reads this every cycle and can write to it.
"""
import json
import os
from datetime import datetime, timezone

KNOWLEDGE_FILE = "data/knowledge.json"

CATEGORIES = {
    "token":    "Notes on specific tokens (behavior, risks, patterns)",
    "market":   "Broader market observations and patterns",
    "strategy": "What's working or not working in the trading strategy",
    "warning":  "Red flags, rugs, suspicious behavior to remember",
}


def _load() -> dict:
    if not os.path.exists(KNOWLEDGE_FILE):
        return {cat: [] for cat in CATEGORIES}
    with open(KNOWLEDGE_FILE) as f:
        data = json.load(f)
    for cat in CATEGORIES:
        data.setdefault(cat, [])
    return data


def _save(data: dict):
    os.makedirs("data", exist_ok=True)
    with open(KNOWLEDGE_FILE, "w") as f:
        json.dump(data, f, indent=2)


def add_entry(category: str, content: str) -> str:
    if category not in CATEGORIES:
        return f"Unknown category '{category}'. Valid: {', '.join(CATEGORIES)}"
    data = _load()
    entry = {
        "ts":      datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "content": content.strip(),
    }
    data[category].append(entry)
    # Keep last 50 entries per category to avoid unbounded growth
    data[category] = data[category][-50:]
    _save(data)
    return f"Saved to {category} knowledge."


def get_summary() -> str:
    data = _load()
    lines = []
    for cat, entries in data.items():
        if not entries:
            continue
        lines.append(f"\n### {cat.upper()} NOTES")
        for e in entries[-10:]:  # show last 10 per category in prompt
            lines.append(f"  [{e['ts']}] {e['content']}")
    if not lines:
        return "No knowledge entries yet."
    return "\n".join(lines)


def get_all() -> dict:
    return _load()
