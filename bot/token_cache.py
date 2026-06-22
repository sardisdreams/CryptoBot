"""
Caches CoinGecko token info (contract address, decimals) locally.
Once a token is looked up, it never hits the API again.
"""
import json
import os
import time

CACHE_FILE = "data/token_cache.json"


def _load() -> dict:
    if not os.path.exists(CACHE_FILE):
        return {}
    with open(CACHE_FILE) as f:
        return json.load(f)


def _save(data: dict):
    os.makedirs("data", exist_ok=True)
    tmp = CACHE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, CACHE_FILE)


def get(cg_id: str) -> dict | None:
    return _load().get(cg_id)


def store(cg_id: str, address: str, decimals: int, name: str, price: float = 0, symbol: str = ""):
    cache = _load()
    cache[cg_id] = {
        "address":   address,
        "decimals":  decimals,
        "name":      name,
        "symbol":    symbol.upper() if symbol else name.upper()[:6],
        "price":     price,
        "cached_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _save(cache)


def get_by_symbol(symbol: str) -> dict | None:
    """Look up a cached token by its symbol (e.g. 'VIRTUAL')."""
    sym = symbol.upper()
    for entry in _load().values():
        if entry.get("symbol", "").upper() == sym:
            return entry
    return None


def list_all() -> dict:
    return _load()
