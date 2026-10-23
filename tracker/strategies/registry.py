"""Versioned, code-defined descriptions used by all generic strategy pages.

These are read-only facts about the released strategy; no user/admin rule editor
and no implicit conversion from a mutable database StrategyPreset.
"""
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType

from tracker.strategies.all_weather.v1 import VERSION as ALL_WEATHER_VERSION, WEIGHTS as ALL_WEATHER_WEIGHTS
from tracker.strategies.dual_momentum.v1 import VERSION as DUAL_MOMENTUM_VERSION
from tracker.strategies.hfea.v1 import VERSION as HFEA_VERSION, WEIGHTS as HFEA_WEIGHTS
from tracker.strategies.nasdaq_trend_dca.v1 import VERSION as NASDAQ_TREND_DCA_VERSION
from tracker.strategies.three_sig.v1 import VERSION as THREE_SIG_VERSION
from tracker.strategies.golden_butterfly.v1 import (
    VERSION as GOLDEN_BUTTERFLY_VERSION, WEIGHTS as GOLDEN_BUTTERFLY_WEIGHTS,
)
from tracker.strategies.permanent_portfolio.v1 import VERSION as PERMANENT_V1_VERSION
from tracker.strategies.permanent_portfolio.v2 import (
    VERSION as PERMANENT_VERSION, WEIGHTS as PERMANENT_WEIGHTS,
)
from tracker.strategies.nine_sig.v2 import VERSION as NINE_SIG_VERSION
from tracker.strategies.nine_sig.v3 import VERSION as NINE_SIG_V3_VERSION


@dataclass(frozen=True)
class StrategyDescription:
    key: str
    version: str
    title: str
    summary: str
    cadence: str
    rules: tuple[str, ...]
    role_targets: tuple[tuple[str, Decimal], ...]
    target_explanation: str
    caution: str
    release_label: str = ""
    version_history: tuple[str, ...] = ()
    # Rule version for new instances per account currency, where it differs
    # from ``version`` (the pound-account version): ((currency, version), ...).
    versions_by_currency: tuple[tuple[str, str], ...] = ()
    # Superseded versions that existing instances may still be pinned to (no
    # new instance pins them; changing an instance needs an explicit upgrade).
    earlier_versions: tuple[str, ...] = ()

    def version_for(self, currency="GBP"):
        return dict(self.versions_by_currency).get(currency, self.version)

    def accepts(self, version):
        """A released rule version an instance may be pinned to."""
        return (version == self.version or version in dict(self.versions_by_currency).values()
                or version in self.earlier_versions)


_RELEASED = {
    "nasdaq-quarterly-target": StrategyDescription(
        key="nasdaq-quarterly-target",
        version=NINE_SIG_VERSION,
        title="9Sig quarterly target",
        summary=("Quarterly target-line tracking with a leveraged growth sleeve "
                 "and a defensive sleeve. All transactions are made by you."),
        cadence="Review completed calendar quarters; urgent events are review-only warnings.",
        rules=(
            "At the first purchase or a verified reset, the baseline is 60% growth and 40% defensive.",
            "The growth signal line rises by 9% each completed quarter, plus half the actual contributions made during that quarter.",
            "Trade proposals use the verified quarter-end holdings, GBP prices, cash and that quarter's target line.",
            "Shortfalls between 90%-100% of reserve use the 81% throttle; over-reserve buys exhaust it with an advisory 60/40 review.",
            "The 30 Down reference uses SPY versus the previous eight quarter-end closes; the next two otherwise-required growth sells can be skipped.",
            "An eight-quarter expiry or third deferred sell requires a verified reset review, not an automatic trade.",
            "A 100%-up TQQQ reference event is a provisional manual-review warning until the actual reference is confirmed.",
            "Only owner-confirmed actual fills are recorded; ordinary quarterly trades do not create a 60/40 reset baseline.",
        ),
        role_targets=(("growth", Decimal("0.60")), ("safe", Decimal("0.40"))),
        target_explanation=("60/40 describes the initial or confirmed-reset mix. "
                            "It is NOT a standing allocation target every quarter."),
        caution=("LQQ3 is not TQQQ, and ERNS is not AGG. Real-quarter workbook "
                 "parity and personal Trading 212 ISA permissions remain unverified."),
        release_label="nine-sig@2",
        versions_by_currency=(("USD", NINE_SIG_V3_VERSION), ("EUR", NINE_SIG_V3_VERSION)),
        version_history=(
            "nine-sig@3 (nine_sig.v3.0.0): the v2 rules unchanged, for dollar and euro accounts.",
            "nine-sig@2 (nine_sig.v2.0.0): all-in shortage, SPY 30 Down kept, "
            "TQQQ drawdown information only, no sell-half.",
            "nine-sig@1 (nine_sig.v1.0.0): frozen original with shortage reset/HOLD.",
        ),
    ),
    "nasdaq-trend-dca": StrategyDescription(
        key="nasdaq-trend-dca",
        version=NASDAQ_TREND_DCA_VERSION,
        title="3x Nasdaq Trend + DCA",
        summary=("Daily SPY/QQQ 200-session trend switching between 3x Nasdaq-100, "
                 "unleveraged Nasdaq-100 and cash, with twelve monthly DCA instalments "
                 "after a bear-market exit. All fills are entered by you."),
        cadence="Daily US market-close checks; monthly DCA when below the exit threshold.",
        rules=(
            "SPY above 104% of its own 200-session moving average: enter 3x Nasdaq.",
            "SPY below 97% of its average: sell 3x, hold proceeds in cash.",
            "While outside 3x, gradually buy unleveraged Nasdaq in 12 equal monthly cash instalments.",
            "A new SPY bullish entry ends DCA and returns to 3x Nasdaq.",
            "While bullish, QQQ above 130% of its average reduces to unleveraged Nasdaq; above 140% exits to cash.",
            "Market signals use the USD reference indexes. Actual fills use the approved account-currency instruments.",
            "These are daily-reset leveraged products. Maximum loss can be substantial; Wealtharr never places trades.",
        ),
        role_targets=(("leveraged", Decimal("0")), ("unleveraged", Decimal("0"))),
        target_explanation="Allocation changes with the trend; weights are not fixed.",
        caution=("Extremely high risk. Historic TQQQ backtests are not LQQ3 returns. "
                 "A moving-average exit cannot eliminate crash, overnight or execution risk. "
                 "DCA funds are sale proceeds, not new contributions."),
        release_label="nasdaq-trend-dca@1",
        version_history=("nasdaq-trend-dca@1 (nasdaq_trend_dca.v1.0.0): fixed daily rules.",),
    ),
    "all-weather": StrategyDescription(
        key="all-weather",
        version=ALL_WEATHER_VERSION,
        title="All Seasons",
        summary=("The All Seasons mix as published in Tony Robbins' MONEY: Master the Game (2014), "
                 "a simplified retail version of Ray Dalio's ideas, not Bridgewater's leveraged "
                 "institutional All Weather fund. Put back to its weights once a year: stocks, "
                 "long-term and intermediate government bonds, gold and broad commodities. "
                 "All transactions are made by you."),
        cadence="One review after each calendar year end.",
        rules=(
            "Target weights, as published: 30% US stocks, 40% long-term government bonds, 15% intermediate government bonds, 7.5% gold, 7.5% broad commodities.",
            "The original uses US Treasuries; in a UK account the bond roles use the UK equivalent, gilts. Each fund is shown with its evidence at setup.",
            "After each 31 December, holdings are valued at verified year-end GBP closes.",
            "Funds above their weight are sold back to it; the proceeds and any cash buy the funds below their weight.",
            "Buys never exceed the cash available; orders under £1 are skipped as too small to place.",
            "New money waits as cash until the next yearly review, or you can split it at the weights yourself.",
            "Only owner-confirmed actual fills are recorded. No trade is ever placed for you.",
        ),
        role_targets=ALL_WEATHER_WEIGHTS,
        target_explanation="These weights are restored at every yearly review.",
        caution=("Not affiliated with Tony Robbins, Ray Dalio or Bridgewater Associates. The rules are the published ones; the funds "
                 "are local equivalents, so results differ from the US original (gilts instead of US "
                 "Treasuries, an S&P 500 fund instead of the total US market). Your own account's "
                 "access to each fund is unverified."),
        release_label="all-weather@1",
        version_history=("all-weather@1 (all_weather.v1.0.0): yearly rebalance to fixed weights.",),
    ),
    "golden-butterfly": StrategyDescription(
        key="golden-butterfly",
        version=GOLDEN_BUTTERFLY_VERSION,
        title="Golden Butterfly",
        summary=("The published Golden Butterfly mix, five equal parts put back to 20% each once a "
                 "year: US large-cap stocks, small-cap value, long-term and short-term government "
                 "bonds, and gold. All transactions are made by you."),
        cadence="One review after each calendar year end.",
        rules=(
            "Target weights, as published: 20% US large-cap stocks (Portfolio Charts lists large-cap blend; earlier write-ups use the total market), 20% US small-cap value, 20% long-term government bonds, 20% short-term government bonds, 20% gold.",
            "The original uses US Treasuries; in a UK account the bond roles use the UK equivalent, gilts. Each fund is shown with its evidence at setup.",
            "After each 31 December, holdings are valued at verified year-end GBP closes.",
            "Funds above their weight are sold back to it; the proceeds and any cash buy the funds below their weight.",
            "Buys never exceed the cash available; orders under £1 are skipped as too small to place.",
            "New money waits as cash until the next yearly review, or you can split it at the weights yourself.",
            "Only owner-confirmed actual fills are recorded. No trade is ever placed for you.",
        ),
        role_targets=GOLDEN_BUTTERFLY_WEIGHTS,
        target_explanation="These weights are restored at every yearly review.",
        caution=("Not affiliated with Portfolio Charts. The rules are the published ones; the funds are "
                 "local equivalents, so results differ from the US original (outside the US, local "
                 "government bonds instead of US Treasuries). Your own account's "
                 "access to each fund is unverified."),
        release_label="golden-butterfly@1",
        version_history=("golden-butterfly@1 (golden_butterfly.v1.0.0): yearly rebalance to five equal weights.",),
    ),
    "dual-momentum": StrategyDescription(
        key="dual-momentum",
        version=DUAL_MOMENTUM_VERSION,
        title="Dual Momentum",
        summary=("Global Equities Momentum as published: each month, hold 100% of whichever is "
                 "strongest of US shares and world shares outside the US, or bonds when shares "
                 "aren't beating cash. All transactions are made by you."),
        cadence="One review after each calendar month end.",
        rules=(
            "At each month end, compare 12-month total returns from verified month-end closes.",
            "Absolute momentum: if US shares returned more than cash, stay in shares; otherwise hold bonds.",
            "Relative momentum: in shares, hold US shares or world ex-US shares, whichever returned more.",
            "Everything goes into the chosen fund: sell any other fund, then buy it with the proceeds and your cash.",
            "The original compares US-dollar returns with US Treasury bills and holds US aggregate bonds; in a UK account the returns are in pounds, cash is a sterling overnight-rate fund and the bond fund is the local equivalent. Each fund is shown with its evidence at setup.",
            "New money is invested in the chosen fund at the next monthly review. Orders under £1 are skipped as too small to place.",
            "Only owner-confirmed actual fills are recorded. No trade is ever placed for you.",
        ),
        role_targets=(("us", Decimal("0")), ("intl", Decimal("0")), ("bonds", Decimal("0"))),
        target_explanation="No fixed weights: 100% sits in the one fund the monthly rules choose.",
        caution=("Not affiliated with Gary Antonacci. The rules are the published ones; the funds and the "
                 "cash series are local equivalents, so switches can differ from the US original. "
                 "Momentum switches can lag sharp turns and trigger capital gains outside an ISA. "
                 "Your own account's access to each fund is unverified."),
        release_label="dual-momentum@1",
        version_history=("dual-momentum@1 (dual_momentum.v1.0.0): monthly Global Equities Momentum.",),
    ),
    "permanent-portfolio": StrategyDescription(
        key="permanent-portfolio",
        version=PERMANENT_VERSION,
        title="Permanent Portfolio",
        summary=("Harry Browne's Permanent Portfolio as published: 25% each in stocks, long-term "
                 "government bonds, cash and gold, left alone unless a yearly check finds one part has drifted too far. "
                 "All transactions are made by you."),
        cadence="A band check once a year, after 31 December; trades only when a part is outside 15-35%.",
        rules=(
            "Target weights, as published: 25% stocks, 25% long-term government bonds, 25% cash (Treasury bills), 25% gold.",
            "Once a year, after 31 December, as Browne wrote, each part's share is measured at verified closes, counting uninvested cash in the total.",
            "If every part is between 15% and 35%, nothing happens.",
            "If any part is below 15% or above 35%, all four are put back to 25%: sells first, then buys, never borrowing.",
            "The original uses US shares, 30-year Treasuries and T-bills. In a UK account the bonds are long gilts and cash is a sterling ultrashort bond fund; in a euro account, long euro government bonds and an overnight-rate fund. Each fund is shown with its evidence at setup.",
            "New money waits as cash until the next rebalance, or you can split it at 25% yourself. Orders under 1 (pound, dollar or euro) are skipped as too small to place.",
            "Only owner-confirmed actual fills are recorded. No trade is ever placed for you.",
        ),
        role_targets=PERMANENT_WEIGHTS,
        target_explanation="25% each, restored only when a part leaves its 15-35% band.",
        caution=("Not affiliated with Harry Browne's estate or publishers. The rules are the published ones; "
                 "the funds are local equivalents (an S&P 500 fund for the US market, and outside the US "
                 "a bond-based cash fund for T-bills), so results differ from the original. Your own "
                 "account's access to each fund is unverified."),
        release_label="permanent-portfolio@2",
        version_history=(
            "permanent-portfolio@1 (permanent_portfolio.v1.0.0): 25% each, 15-35% bands, checked after each month end.",
            "permanent-portfolio@2 (permanent_portfolio.v2.0.0): the same bands, checked once a year as Browne "
            "wrote. Strategies already pinned to version 1 keep the monthly check until explicitly upgraded.",
        ),
        earlier_versions=(PERMANENT_V1_VERSION,),
    ),
    "hfea": StrategyDescription(
        key="hfea",
        version=HFEA_VERSION,
        title="HFEA",
        summary=("HEDGEFUNDIE's Excellent Adventure as published on the Bogleheads forum (2019): 55% "
                 "in a 3x S&P 500 fund and 45% in a 3x long-term US Treasury fund, put back to those "
                 "weights every quarter. Very high risk: it lost roughly 70% from its peak in 2022. "
                 "All transactions are made by you."),
        cadence="One rebalance after each calendar quarter end.",
        rules=(
            "Target weights, as published (HEDGEFUNDIE's own revision of his first 40/60): 55% 3x daily S&P 500, 45% 3x daily long-term US Treasuries.",
            "After each quarter end (31 March, 30 June, 30 September, 31 December), holdings are valued at verified closes.",
            "Funds above their weight are sold back to it; the proceeds and any cash buy the fund below its weight. No bands: every quarter goes back to 55/45.",
            "The original funds are UPRO and TMF (US). In a UK account the stock side is WisdomTree's 3x S&P 500 (3LUS) and the bond side is WisdomTree's 3x 10-year US Treasuries (3TYL): no 3x 20+ year Treasury fund trades in Europe, so the bond side reacts about 40% as strongly to rate changes as TMF. Each fund is shown with its evidence at setup.",
            "New money waits as cash until the next quarterly rebalance, or you can split it 55/45 yourself (the original sets no rule for new money). Orders under 1 (pound or dollar) are skipped as too small to place.",
            "Only owner-confirmed actual fills are recorded. No trade is ever placed for you.",
        ),
        role_targets=HFEA_WEIGHTS,
        target_explanation="55/45 is restored at every quarterly rebalance.",
        caution=("Not affiliated with HEDGEFUNDIE or the Bogleheads forum. Leveraged daily funds lose value "
                 "in choppy markets and can fall far more than the market; stocks and bonds fell together "
                 "in 2022. In a UK account the bond fund is a 10-year, not a 20+ year, 3x fund (a material "
                 "difference from the original). Brokers may ask knowledge questions before you can buy "
                 "leveraged products. Your own account's access to each fund is unverified."),
        release_label="hfea@1",
        version_history=("hfea@1 (hfea.v1.0.0): 55/45 3x stocks/3x long Treasuries, quarterly rebalance.",),
    ),
    "three-sig": StrategyDescription(
        key="three-sig",
        version=THREE_SIG_VERSION,
        title="3Sig",
        summary=("Jason Kelly's 3% Signal (The 3% Signal, 2015): a small-company stock fund and a bond "
                 "fund, 80/20 to start. Each quarter the stock side is steered to a 3% growth target, "
                 "selling the surplus into bonds or buying the shortfall from them. Run with the same "
                 "later rules as the app's 9Sig. All transactions are made by you."),
        cadence="Review completed calendar quarters; a reset always waits for you to confirm it.",
        rules=(
            "At the first purchase or a confirmed reset, the baseline is 80% small-company stocks and 20% bonds and cash.",
            "The stock signal line rises by 3% each completed quarter, plus half the actual deposits made during that quarter.",
            "After each quarter end, stocks above the line are sold into bonds; a shortfall is bought with bonds and cash.",
            "When bonds and cash pass 30% of the plan, the review asks for a reset to 80/20.",
            "30 Down: after SPY closes 30% below its highest of the previous eight quarter ends, the next two stock sell signals are skipped; a third would-be sell, or eight quarters passing, asks for a reset.",
            "Shortfalls between 90% and 100% of the bond reserve buy only 81% of it; a shortfall above the reserve uses it all and suggests reviewing a reset.",
            "The book's fund is IJR and its bond fund AGG. In a UK account the stock side is iShares S&P SmallCap 600 (ISP6, the same index) and the bond side is ERNS, an ultrashort sterling bond fund, as in 9Sig. Each fund is shown with its evidence at setup.",
            "Only owner-confirmed actual fills are recorded; ordinary quarterly trades do not create a reset baseline. No trade is ever placed for you.",
        ),
        role_targets=(("smallcap", Decimal("0.80")), ("safe", Decimal("0.20"))),
        target_explanation=("80/20 describes the initial or confirmed-reset mix. "
                            "It is NOT a standing allocation target every quarter."),
        caution=("Not affiliated with Jason Kelly or The Kelly Letter. Where the book differs from Kelly's "
                 "later 9Sig, this follows 9Sig by your choice: the book skips the next four sell signals "
                 "after 30 Down (here two, then a reset) and has no large-buy throttle. ERNS (and ERNX for "
                 "euros) is an ultrashort bond fund, not the book's AGG, so the bond side barely moves. "
                 "Your own account's access to each fund is unverified."),
        release_label="three-sig@1",
        version_history=("three-sig@1 (three_sig.v1.0.0): 3% quarterly signal, 80/20, 9Sig-style 30 Down "
                         "(two skips), throttle and resets.",),
    ),
}
STRATEGIES = MappingProxyType(_RELEASED)


# One colour per strategy, used everywhere it appears (sidebar, page, charts).
COLOURS = {
    "nasdaq-quarterly-target": "#5b9cf5", "all-weather": "#4fc3a1", "golden-butterfly": "#e06c9f",
    "dual-momentum": "#e5c07b", "nasdaq-trend-dca": "#ff956b", "permanent-portfolio": "#56b6c2", "hfea": "#c678dd", "three-sig": "#98c379",
}
DEFAULT_COLOUR = "#8a94a3"


def colour_for(instance):
    return COLOURS.get(getattr(instance, "strategy_key", "") or instance.preset.slug, DEFAULT_COLOUR)


def describe_strategy(key: str) -> StrategyDescription | None:
    """Resolve a released description or refuse unknown mutable preset rules."""
    return STRATEGIES.get(key)


# Plug-ins, keyed like the descriptions. Generic code finds a strategy's
# behaviour here and never imports a strategy's rules directly.
def _plugins():
    from tracker.strategies.all_weather.plugin import ALL_WEATHER
    from tracker.strategies.dual_momentum.plugin import DUAL_MOMENTUM
    from tracker.strategies.golden_butterfly.plugin import GOLDEN_BUTTERFLY
    from tracker.strategies.nine_sig.plugin import NINE_SIG
    from tracker.strategies.permanent_portfolio.plugin import PERMANENT_PORTFOLIO
    from tracker.strategies.hfea.plugin import HFEA
    from tracker.strategies.nasdaq_trend_dca.plugin import NASDAQ_TREND_DCA
    from tracker.strategies.three_sig.plugin import THREE_SIG
    return {p.key: p for p in (NINE_SIG, ALL_WEATHER, GOLDEN_BUTTERFLY, DUAL_MOMENTUM, PERMANENT_PORTFOLIO,
                               HFEA, THREE_SIG, NASDAQ_TREND_DCA)}


_MODULES = {}
_EXTRA = {}  # Test-only registrations; never used in production code paths.


def _all():
    if not _MODULES:
        _MODULES.update(_plugins())
    return {**_MODULES, **_EXTRA}


def module_for_key(key):
    return _all().get(key)


def module_for(instance):
    """The plug-in for a strategy instance, or None for an unknown strategy.

    An instance pinned to an older rule version the plug-in still lists gets
    that version's plug-in, so its schedule and reviews don't change under it.
    """
    plugin = module_for_key(getattr(instance, "strategy_key", "") or instance.preset.slug)
    if plugin is None:
        return None
    return plugin.by_version.get(getattr(instance, "strategy_version", ""), plugin)


def plugin_keys():
    return tuple(_all())


def last_review_before(instance, day):
    """The strategy's last completed review date before ``day``."""
    from tracker.core.calendar import quarter_end_before
    plugin = module_for(instance)
    return (plugin.last_review_before if plugin else quarter_end_before)(day)


def next_review(instance, day):
    from tracker.core.calendar import quarter_end_on_or_after
    plugin = module_for(instance)
    return (plugin.next_review if plugin else quarter_end_on_or_after)(day)
