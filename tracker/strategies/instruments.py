"""A strategy instance's funds, read generically from its pinned manifest.

Any number of roles. Instances from before manifests existed fall back to
their two legacy columns (growth = equity_symbol, safe = safe_symbol).
"""
ROLE_LABELS = {
    "growth": "Growth", "safe": "Safe",
    "stocks": "Equities", "long": "Long bonds", "inter": "Intermediate bonds",
    "gold": "Gold", "comm": "Commodities", "bonds": "Bonds", "cash": "Cash",
    "scv": "Small-cap value", "short": "Short bonds",
    "us": "US shares", "intl": "World ex-US shares",
}


def role_symbols(instance):
    """Ordered {role: Yahoo ticker} for the instance's pinned funds."""
    roles = (getattr(instance, "resolved_instruments", None) or {}).get("roles") or {}
    if roles:
        from .registry import describe_strategy
        description = describe_strategy(getattr(instance, "strategy_key", "") or instance.preset.slug)
        order = [role for role, _ in description.role_targets] if description else []
        ordered = [r for r in order if r in roles] + sorted(r for r in roles if r not in order)
        return {role: roles[role]["yahoo_ticker"] for role in ordered if roles[role].get("yahoo_ticker")}
    legacy = {"growth": instance.equity_symbol, "safe": instance.safe_symbol}
    return {role: symbol for role, symbol in legacy.items() if symbol}


def symbols(instance):
    return tuple(dict.fromkeys(role_symbols(instance).values()))


def symbol_labels(instance):
    """{ticker: "Growth"} etc., for holdings tables."""
    return {symbol: ROLE_LABELS.get(role, role.title()) for role, symbol in role_symbols(instance).items()}
