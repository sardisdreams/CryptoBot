"""
Smoke tests: import every critical module.
Catches NameErrors, SyntaxErrors, and broken imports before they reach production.
A module that fails to import will crash the bot on startup.
"""


def test_import_config():
    import bot.config  # noqa: F401


def test_import_signals():
    import bot.signals  # noqa: F401


def test_import_history():
    import bot.history  # noqa: F401


def test_import_positions():
    import bot.positions  # noqa: F401


def test_import_risk():
    import bot.risk  # noqa: F401


def test_import_capital():
    import bot.capital  # noqa: F401


def test_import_cost_tracker():
    import bot.cost_tracker  # noqa: F401


def test_import_performance():
    import bot.performance  # noqa: F401


def test_import_screener():
    import bot.screener  # noqa: F401


def test_import_evaluator():
    import bot.evaluator  # noqa: F401


def test_import_blacklist():
    import bot.blacklist  # noqa: F401


def test_import_token_cache():
    import bot.token_cache  # noqa: F401


def test_import_knowledge():
    import bot.knowledge  # noqa: F401


def test_import_agent():
    import bot.agent  # noqa: F401


def test_import_audit():
    import bot.audit  # noqa: F401
