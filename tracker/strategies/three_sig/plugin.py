"""3Sig as a strategy plug-in: the shared quarterly signal engine with 3Sig's rules."""
from decimal import Decimal

from tracker.core.calendar import quarter_end_before, quarter_end_on_or_after
from tracker.strategies.base import StrategyModule
from tracker.strategies.three_sig import v1
from tracker.study_fixed import Source
from tracker.study_three_sig import ThreeSigStudy

KEY = "three-sig"


def _review(instance):
    from tracker.market_signals import review_strategy
    return review_strategy(instance)


IJR = dict(proxy="IJR", proxy_label="IJR (same index, in US dollars)")


# One Study per account currency. Before the pound and euro small-cap lines
# launched they follow IJR (same index); a bond line's earlier days are cash at 0%.
STUDY = {
    "GBP": ThreeSigStudy(rules=v1, sources={
        "smallcap": Source("ISP6.L", "S&P SmallCap 600 (ISP6)", **IJR),
        "safe": Source("ERNS.L", "ultrashort bonds (ERNS)"),
    }),
    "USD": ThreeSigStudy(rules=v1, currency="USD", sources={
        "smallcap": Source("IJR", "S&P SmallCap 600 (IJR)"),
        "safe": Source("AGG", "US aggregate bonds (AGG)"),
    }),
    "EUR": ThreeSigStudy(rules=v1, currency="EUR", sources={
        "smallcap": Source("IUS3.DE", "S&P SmallCap 600 (IUS3)", **IJR),
        "safe": Source("ERNX.DE", "euro ultrashort bonds (ERNX)"),
    }),
}

THREE_SIG = StrategyModule(
    key=KEY,
    review=_review,
    next_review=quarter_end_on_or_after,
    last_review_before=quarter_end_before,
    reference_symbols=("SPY",),
    allocation_label="Reset",
    supports_reset=True,
    reset_equity_fraction=Decimal("0.80"),
    study=STUDY,
)
