"""Independent Stooq reference-price fallback, never a UK execution quote.

Source 1 is Yahoo; this adapter is invoked only when its USD reference data
cannot be obtained. Stooq's CSV has no currency metadata: only pinned US USD
tickers are allowed AND an existing independently Yahoo-verified overlapping
close must corroborate the price scale. Without that, refuse the fallback.
No broker calls, no persistence, no price fabrication.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
import io
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from tracker.strategies.nine_sig.v1 import NormalizedQuote, quarter_number

STOOQ_REFERENCE_MAP = {"TQQQ": "tqqq.us", "SPY": "spy.us"}
MAX_REFERENCE_DIFFERENCE = Decimal("0.01")
MAX_DOWNLOAD_BYTES = 2_000_000



@dataclass(frozen=True)
class PriceSanityDecision:
    """Only APPROVED observations may be persisted or fed to a strategy."""
    status: str             # APPROVED or HOLD
    code: str
    explanation: str


def check_price_series(
    symbol: str,
    series: tuple[tuple[date, Decimal], ...] | list[tuple[date, Decimal]],
    *,
    as_of: date,
    previous: tuple[date, Decimal] | None = None,
    max_daily_move: Decimal | None = None,
    max_age_days: int = 8,
) -> PriceSanityDecision:
    """Fail closed on anomalous or stale *already unit-normalized* vendor closes.

    No 'corrected' price is invented and no suspicious observation is persisted.
    Any suspected split/restrike/pence error needs reviewed evidence before
    clearing the hold. This policy intentionally errs on false positives.
    """
    def hold(code: str, message: str) -> PriceSanityDecision:
        return PriceSanityDecision("HOLD", code, message)

    # Daily 3x leveraged products can move much more than unleveraged funds.
    # Threshold is an anomaly screen, not a trading or return-limit rule.
    if max_daily_move is None:
        max_daily_move = (Decimal("0.85") if symbol in ("LQQ3.L", "TQQQ")
                          else Decimal("0.40"))
    if (not isinstance(max_daily_move, Decimal) or
            not max_daily_move.is_finite() or
            max_daily_move <= 0 or max_daily_move >= 1 or
            max_age_days < 0):
        return hold("INVALID_POLICY", "Market-data safety policy is invalid")
    if not series:
        return hold("MISSING_PRICE", "No usable close from the price provider")
    ordered = tuple(sorted(series))
    if len({day for day, _ in ordered}) != len(ordered):
        return hold("DUPLICATE_DATE", "Quote dates conflict")
    if any(not isinstance(day, date) or not isinstance(price, Decimal) or
           not price.is_finite() or price <= 0 or day > as_of
           for day, price in ordered):
        return hold("BAD_PRICE", "Quote contains an invalid price or future date")
    if as_of - ordered[-1][0] > timedelta(days=max_age_days):
        return hold("STALE_PRICE", "Latest quote is too old for a review")

    predecessor = previous
    for day, price in ordered:
        if predecessor is not None:
            previous_date, previous_price = predecessor
            if (not isinstance(previous_date, date) or
                    not isinstance(previous_price, Decimal) or
                    not previous_price.is_finite() or previous_price <= 0):
                return hold("BAD_PRIOR_PRICE", "Previous accepted close is invalid")
            if day <= previous_date:
                # Overlapping historical refreshes are common. Do not compare
                # a historic quote to a more recent database observation.
                predecessor = (day, price)
                continue
            change = abs(price / previous_price - Decimal("1"))
            if change > max_daily_move:
                return hold(
                    "LARGE_MOVE",
                    "Market price change exceeds the safety threshold; "
                    "check splits/restrikes, quote units and source",
                )
        predecessor = (day, price)
    return PriceSanityDecision("APPROVED", "", "")


class QuoteUnavailable(ValueError):
    """Missing, uncorroborated or incompatible market data."""


def fetch_stooq_reference(symbol: str, since: date, through: date,
                          *, yahoo_anchor: NormalizedQuote) -> tuple[NormalizedQuote, ...]:
    """Fetch daily USD reference history or fail closed without touching storage.

    The Yahoo anchor must overlap an actual Stooq observation. Prices differing
    by over 1% are refused (corporate-action adjustment mismatch included).
    Returned quotes are independently source-labelled and scale-corrobated.
    """
    if symbol not in STOOQ_REFERENCE_MAP:
        raise QuoteUnavailable("Stooq fallback supports pinned USD references only")
    if not isinstance(since, date) or not isinstance(through, date) or since > through:
        raise QuoteUnavailable("Invalid independent provider date range")
    if (yahoo_anchor.symbol != symbol or yahoo_anchor.currency != "USD" or
            yahoo_anchor.unit != "USD" or yahoo_anchor.raw_unit != "USD" or
            yahoo_anchor.source != "yahoo" or not yahoo_anchor.raw_unit_verified or
            not yahoo_anchor.price.is_finite() or yahoo_anchor.price <= 0 or
            yahoo_anchor.as_of > through or
            through - yahoo_anchor.as_of > timedelta(days=14)):
        raise QuoteUnavailable("Recent USD Yahoo anchor required before independent fallback")
    # Include the trusted anchor day even if older than requested data.
    from_day = min(since, yahoo_anchor.as_of)
    params = urlencode({
        "s": STOOQ_REFERENCE_MAP[symbol], "i": "d",
        "d1": from_day.strftime("%Y%m%d"), "d2": through.strftime("%Y%m%d"),
    })
    request = Request("https://stooq.com/q/d/l/?" + params, headers={
        "User-Agent": "Wealtharr/2 reference sanity-check", "Accept": "text/csv"
    })
    try:
        with urlopen(request, timeout=12) as response:
            if response.status != 200:
                raise QuoteUnavailable("Independent quote provider unavailable")
            data = response.read(MAX_DOWNLOAD_BYTES + 1)
    except (OSError, TimeoutError) as exc:
        raise QuoteUnavailable("Independent quote provider unavailable") from exc
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise QuoteUnavailable("Independent quote response exceeds safety limit")
    try:
        reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
        if not reader.fieldnames or not {"Date", "Close"}.issubset(reader.fieldnames):
            raise QuoteUnavailable("Independent quote columns missing")
        quotes = []
        dates = set()
        for row in reader:
            observed = date.fromisoformat(row["Date"])
            value = Decimal(row["Close"])
            if (observed in dates or not value.is_finite() or value <= 0 or
                    observed < from_day or observed > through):
                raise QuoteUnavailable("Invalid independent quote date/price/duplicate")
            dates.add(observed)
            quotes.append((observed, value))
    except (UnicodeError, ValueError, TypeError, InvalidOperation, KeyError) as exc:
        raise QuoteUnavailable("Invalid independent quote response") from exc
    match = next((value for day, value in quotes if day == yahoo_anchor.as_of), None)
    if match is None or abs(match / yahoo_anchor.price - Decimal("1")) > MAX_REFERENCE_DIFFERENCE:
        raise QuoteUnavailable("Yahoo and independent reference scales conflict")
    accepted = tuple(
        NormalizedQuote(symbol, day, value, "USD", "USD", "stooq", "USD", True,
                        raw_close=value, observation_id="stooq:%s:%s" % (symbol, day))
        for day, value in sorted(quotes) if since <= day <= through
    )
    if not accepted:
        raise QuoteUnavailable("Independent provider has no observations in the requested period")
    return accepted


def reference_with_fallback(symbol: str, since: date, through: date, *,
                            yahoo_quotes: tuple[NormalizedQuote, ...],
                            yahoo_anchor: NormalizedQuote | None = None,
                            fallback_fetch=fetch_stooq_reference) -> tuple[NormalizedQuote, ...]:
    """Select primary USD reference history or independently corroborated Stooq.

    Fallback never guesses UK GBX/GBP, never writes MarketObservation/PriceMark,
    and never relabels Stooq observations as Yahoo.
    """
    if symbol not in STOOQ_REFERENCE_MAP:
        raise QuoteUnavailable("Only pinned USD references can use this fallback")
    def valid(q):
        return (q.symbol == symbol and q.source == "yahoo" and
                q.currency == q.unit == q.raw_unit == "USD" and
                q.raw_unit_verified and isinstance(q.price, Decimal) and
                q.price.is_finite() and q.price > 0 and
                q.raw_close == q.price and q.observation_id and
                since <= q.as_of <= through)
    needed_quarters = set(range(quarter_number(since), quarter_number(through) + 1))
    def covers(quotes):
        present = {quarter_number(q.as_of) for q in quotes}
        return (needed_quarters.issubset(present) and
                max(q.as_of for q in quotes) >= through - timedelta(days=6))
    if yahoo_quotes and all(valid(q) for q in yahoo_quotes) and covers(yahoo_quotes):
        return tuple(sorted(yahoo_quotes, key=lambda q: q.as_of))
    if yahoo_anchor is None:
        raise QuoteUnavailable("An existing independently verified Yahoo anchor is required")
    independent = fallback_fetch(symbol, since, through, yahoo_anchor=yahoo_anchor)
    if not independent or any(not (q.source == "stooq" and
            q.symbol == symbol and q.currency == q.unit == q.raw_unit == "USD" and
            q.raw_unit_verified and q.raw_close == q.price and q.observation_id)
            for q in independent) or not covers(independent):
        raise QuoteUnavailable("Independent reference series lacks required quarter closes")
    return independent
