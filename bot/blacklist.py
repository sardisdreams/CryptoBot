import json
import os

BLACKLIST_FILE = "data/blacklist.json"


def _load() -> dict:
    if not os.path.exists(BLACKLIST_FILE):
        return {"symbols": [], "cg_ids": [], "reasons": {}}
    with open(BLACKLIST_FILE) as f:
        return json.load(f)


def _save(data: dict):
    os.makedirs("data", exist_ok=True)
    with open(BLACKLIST_FILE, "w") as f:
        json.dump(data, f, indent=2)


def block(symbol: str, cg_id: str = "", reason: str = "User blocked"):
    data = _load()
    sym = symbol.upper()
    if sym not in data["symbols"]:
        data["symbols"].append(sym)
    if cg_id and cg_id not in data["cg_ids"]:
        data["cg_ids"].append(cg_id)
    data["reasons"][sym] = reason
    _save(data)


def unblock(symbol: str):
    data = _load()
    sym = symbol.upper()
    data["symbols"] = [s for s in data["symbols"] if s != sym]
    data["reasons"].pop(sym, None)
    _save(data)


def is_blocked(symbol: str, cg_id: str = "") -> bool:
    data = _load()
    if symbol.upper() in data["symbols"]:
        return True
    if cg_id and cg_id in data["cg_ids"]:
        return True
    return False


def get_all() -> dict:
    return _load()
