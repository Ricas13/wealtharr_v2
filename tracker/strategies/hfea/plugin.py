"""HFEA as a strategy plug-in: back to 55/45 after each calendar quarter end."""
from tracker.strategies.hfea import v1
from tracker.strategies.yearly_plugin import yearly_rebalance_plugin
from tracker.study_fixed import FixedWeightStudy, Source

KEY = "hfea"
RULE = "sell what's above its weight, buy what's below, never borrowing."


def _rebuilt(symbol, charge):
    return dict(proxy=symbol, proxy_leverage=3, proxy_charge=charge,
                proxy_label="3x %s's daily moves less %.2f%% a year (borrowing costs not modelled)"
                % (symbol, charge * 100))


# One Study per account currency. Before each 3x fund launched, its history is
# rebuilt from 3x an older fund's daily moves and flagged on the page.
STUDY = {
    "GBP": FixedWeightStudy(title="HFEA", rules=v1, review_word="quarterly", rule_text=RULE, sources={
        "stocks3x": Source("3LUS.L", "3x S&P 500 (3LUS)", **_rebuilt("SPY", 0.0075)),
        "bonds3x": Source("3TYL.L", "3x 10-year Treasuries (3TYL)", **_rebuilt("IEF", 0.003)),
    }),
    "USD": FixedWeightStudy(title="HFEA", rules=v1, review_word="quarterly", rule_text=RULE,
                            currency="USD", sources={
        "stocks3x": Source("UPRO", "3x S&P 500 (UPRO)", **_rebuilt("SPY", 0.0091)),
        "bonds3x": Source("TMF", "3x 20+ year Treasuries (TMF)", **_rebuilt("TLT", 0.0106)),
    }),
}
HFEA = yearly_rebalance_plugin(KEY, v1, study=STUDY, cadence="quarterly")
