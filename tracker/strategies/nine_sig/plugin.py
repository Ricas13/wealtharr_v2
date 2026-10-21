"""9Sig as a strategy plug-in. Wires the existing 9Sig rules into the generic core."""
from tracker.core.calendar import quarter_end_before, quarter_end_on_or_after
from tracker.strategies.base import StrategyModule
from tracker.strategies.nine_sig.v2 import VERSION

KEY = "nasdaq-quarterly-target"


def _review(instance):
    from tracker.market_signals import review_strategy
    return review_strategy(instance)


def _dashboard_context(instance, report, next_end):
    from tracker.strategies.nine_sig.dashboard import growth_target, quarter_rows
    return {
        "dash_target": growth_target(instance, report, next_end),
        "dash_quarters": quarter_rows(instance, next_end, report),
    }


def _manual_reset(**kwargs):
    from tracker.cockpit_manual_reset import choose_manual_reserve_reset
    return choose_manual_reserve_reset(**kwargs)


def _review_detail(instance, signal):
    from tracker.v2_breakdown import _detail
    return _detail(instance, signal)


def _review_export(instance, signal):
    from tracker.v2_breakdown import _detail, _export
    return _export(instance, _detail(instance, signal))


def _offers_manual_reset(signal):
    from tracker.strategies.nine_sig.v3 import VERSION as V3
    return (signal.strategy_version in (VERSION, V3) and
            "Your reserve is now empty. Review whether to reset to 60/40." in signal.message)


class _LazyModule:
    """Imported on first use, so the registry stays importable before Django is ready."""

    def __init__(self, name):
        self._name = name

    def __getattr__(self, attr):
        import importlib
        return getattr(importlib.import_module(self._name), attr)


class _LazyStudy:
    """A non-pound 9Sig study, built on first use."""

    def __init__(self, **lines):
        self._lines = lines
        self._study = None

    def __getattr__(self, attr):
        if self._study is None:
            from tracker.study import Lines, NineSigStudy
            self._study = NineSigStudy(Lines(**self._lines))
        return getattr(self._study, attr)


# One Study per account currency: pounds LQQ3/ERNS (v2), dollars TQQQ/AGG and
# euros 3QQQ/ERNX (v3, the same rules).
STUDY = {
    "GBP": _LazyModule("tracker.study"),
    "USD": _LazyStudy(currency="USD", growth="TQQQ", safe="AGG", growth_name="TQQQ",
                      safe_name="AGG", rules="v3"),
    "EUR": _LazyStudy(currency="EUR", growth="3QQQ.DE", safe="ERNX.DE", growth_name="3QQQ",
                      safe_name="ERNX", rules="v3"),
}

NINE_SIG = StrategyModule(
    key=KEY,
    review=_review,
    next_review=quarter_end_on_or_after,
    last_review_before=quarter_end_before,
    reference_symbols=("TQQQ", "SPY"),
    dashboard_template="strategies/nine_sig/dashboard.html",
    dashboard_context=_dashboard_context,
    allocation_label="Reset",
    supports_reset=True,
    offers_manual_reset=_offers_manual_reset,
    study=STUDY,
    manual_reset=_manual_reset,
    review_detail=_review_detail,
    review_export=_review_export,
)
