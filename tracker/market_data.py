"""Yahoo daily closes for any catalogued fund or reference index.

Strategy-neutral: downloads, screens and stores prices. Never evaluates a
strategy or places an order.
"""
import json
import logging
import re
from datetime import date, datetime, timedelta, timezone as utc_timezone
from decimal import Decimal
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from django.utils import timezone

from .core.prices import check_price_series
from .models import MarketObservation, YahooSync

LOG = logging.getLogger(__name__)
ZERO = Decimal("0")
TICKER = re.compile(r"^[A-Z0-9][A-Z0-9.^=_-]{0,23}$")


@dataclass(frozen=True)
class FetchedChart:
    """Two-value unpacking retained; raw vendor metadata stays attached."""
    currency: str
    series: list
    raw_unit: str
    raw_closes: dict
    dividends: dict = field(default_factory=dict)  # {ex-date: cash per share, account units}

    def __iter__(self):
        yield self.currency
        yield self.series


def fetch_chart(symbol, since):
    """Publicly accessible but unofficial Yahoo endpoint. No authentication bypass."""
    if not TICKER.fullmatch(symbol):
        raise ValueError("Invalid Yahoo symbol")
    params = urlencode({
        "period1": int(datetime.combine(since, datetime.min.time(), utc_timezone.utc).timestamp()),
        "period2": int(timezone.now().timestamp()) + 86400,
        "interval": "1d", "events": "div",
    })
    last_error = None
    chart = None
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        endpoint = "https://" + host + "/v8/finance/chart/" + quote(symbol, safe="") + "?" + params
        req = Request(endpoint, headers={
            "User-Agent": "Mozilla/5.0 (compatible; WealtharrOwnerTracker/0.2)",
            "Accept": "application/json",
        })
        try:
            with urlopen(req, timeout=14) as response:
                if response.status != 200:
                    raise ValueError("Yahoo chart returned a non-200 status")
                payload = json.loads(response.read(4_000_000))
            chart = (payload.get("chart", {}).get("result") or [None])[0]
            if chart:
                break
        except (OSError, ValueError) as exc:
            last_error = exc
        # Alternate official Yahoo chart host is a best-effort fallback, not
        # a market-data SLA or a licence to redistribute historical quotes.
    if chart is None and last_error is not None:
        raise last_error
    if not chart:
        raise ValueError("Yahoo supplied no chart data")
    raw_currency = chart.get("meta", {}).get("currency")
    # Exact pinned product/line unit checks. A Yahoo GBX/GBP metadata change
    # must not create a fictitious 100x portfolio valuation.
    from .core.catalogue import expected_quote_units
    expected_units = expected_quote_units()
    if symbol in expected_units and raw_currency not in expected_units[symbol]:
        raise ValueError("PRICE_HOLD:RAW_UNIT_MISMATCH")
    currency = "GBP" if raw_currency in ("GBp", "GBX", "GBP") else raw_currency
    if currency not in ("GBP", "USD", "EUR"):
        raise ValueError("Unsupported Yahoo quote currency")
    divisor = Decimal(100) if raw_currency in ("GBp", "GBX") else Decimal(1)
    closes = ((chart.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    open_from = _unfinished_session_start(chart.get("meta") or {})
    local = _exchange_zone(chart.get("meta") or {})
    series = []
    raw_closes = {}
    for epoch, raw in zip(chart.get("timestamp") or [], closes):
        if raw is None or epoch >= open_from:
            continue  # no price yet, or today's session hasn't closed: not a close
        try:
            raw_amount = Decimal(str(raw)).quantize(Decimal("0.00000001"))
            amount = (raw_amount / divisor).quantize(Decimal("0.00000001"))
            day = datetime.fromtimestamp(epoch, tz=local).date()
            if amount > ZERO and day <= timezone.localdate():
                series.append((day, amount))
                raw_closes[day] = raw_amount
        except (ValueError, ArithmeticError, OverflowError):
            continue
    series = sorted(dict(series).items())
    if not series:
        raise ValueError("No usable Yahoo daily closes")
    dividends = {}
    for item in ((chart.get("events") or {}).get("dividends") or {}).values():
        try:
            paid = (Decimal(str(item["amount"])) / divisor).quantize(Decimal("0.00000001"))
            day = datetime.fromtimestamp(int(item["date"]), tz=local).date()
        except (KeyError, TypeError, ValueError, ArithmeticError, OverflowError):
            continue
        if paid > ZERO and day <= timezone.localdate():
            dividends[day] = paid
    return FetchedChart(currency, series, raw_currency, raw_closes, dividends)


def _exchange_zone(meta):
    """The exchange's own time zone for dating daily bars.

    Yahoo stamps a daily bar at the session start in exchange time; for
    currencies that's midnight London, which is 23:00 UTC the day before in
    summer. Reading the stamp in UTC would date it a day early.
    """
    name = meta.get("exchangeTimezoneName")
    if isinstance(name, str):
        try:
            return ZoneInfo(name)
        except (ValueError, KeyError, OSError):
            pass
    offset = meta.get("gmtoffset")
    if isinstance(offset, int) and abs(offset) < 86400:
        return utc_timezone(timedelta(seconds=offset))
    return utc_timezone.utc


# Wait this long after the regular session ends before taking its bar as the
# close (closing auctions and Yahoo's own settling).
CLOSE_SETTLE = 30 * 60


def _unfinished_session_start(meta):
    """Epoch from which Yahoo's daily bars are still live prices, not closes.

    During a session Yahoo's daily bar for today is the current price, which
    then changes at the close. Storing it would later look like a revised
    historical close and hold the line. Without session times, today's bar
    (UTC date) is left out to be safe.
    """
    regular = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
    start, end = regular.get("start"), regular.get("end")
    now = timezone.now().timestamp()
    if isinstance(start, (int, float)) and isinstance(end, (int, float)):
        return start if now < end + CLOSE_SETTLE else float("inf")
    today = datetime.now(utc_timezone.utc).date()
    return datetime.combine(today, datetime.min.time(), utc_timezone.utc).timestamp()


# A changed close within this fraction of the stored one is accepted
# automatically (and logged); a bigger change holds the line for review.
# 20% leaves room for leveraged funds and still catches splits (4-for-3 is
# -25%, 3-for-2 -33%, 2-for-1 -50%) and pence/pounds errors (100x).
AUTO_ACCEPT_REVISION = Decimal("0.20")


def apply_revision(revision, user=None):
    """Store Yahoo's close for one logged revision; ``user`` is None when automatic."""
    observation = MarketObservation.objects.select_for_update().filter(
        symbol=revision.symbol, as_of=revision.as_of).first()
    if observation is not None and observation.price == revision.stored_price:
        observation.price = revision.yahoo_price
        if observation.raw_unit_verified:
            factor = 100 if observation.raw_quote_unit in ("GBp", "GBX") else 1
            observation.raw_close = revision.yahoo_raw_close or revision.yahoo_price * factor
        observation.save(update_fields=["price", "raw_close"])
    revision.accepted_at, revision.accepted_by = timezone.now(), user
    revision.save(update_fields=["accepted_at", "accepted_by"])
    # Older pending rows for the same day are superseded by this one.
    type(revision).objects.filter(symbol=revision.symbol, as_of=revision.as_of,
                                  accepted_at__isnull=True).update(accepted_at=revision.accepted_at)


# Yahoo's daily GBP/USD close (US dollars per pound) converts USD-quoted
# holdings to pounds for the same day. No rate that day, no GBP price.
FX_SYMBOL = "GBPUSD=X"
EIGHT_DP = Decimal("0.00000001")


def usd_holding_symbols():
    """Pound-account fund lines quoted in USD (not reference series like SPY).

    Dollar accounts hold USD lines as they are; only pound accounts convert.
    """
    from .core.catalogue import CATALOGUE
    return {line.ticker for line in CATALOGUE.values()
            if line.raw_quote_unit == "USD" and line.currency == "GBP"}


def fx_rates(days):
    """{day: USD per GBP} from verified daily closes, for the days asked."""
    return dict(MarketObservation.objects.filter(
        symbol=FX_SYMBOL, as_of__in=list(days), currency="USD", price__gt=ZERO,
    ).values_list("as_of", "price"))


def usd_to_gbp(amount, rate):
    return (amount / rate).quantize(EIGHT_DP)


def sync_ticker(symbol, *, force=False):
    """One fetch per 12 hours per symbol, with durable prices and bounded errors."""
    if not TICKER.fullmatch(symbol):
        raise ValueError("Invalid Yahoo symbol")
    state, _ = YahooSync.objects.get_or_create(symbol=symbol)
    now = timezone.now()
    if (not force and state.last_attempt and state.last_attempt > now -
            (timedelta(minutes=30) if state.last_error else timedelta(hours=12))):
        return MarketObservation.objects.filter(symbol=symbol).order_by("-as_of").first()
    first = not MarketObservation.objects.filter(symbol=symbol).exists()
    since = date(2010, 1, 1) if first else timezone.localdate() - timedelta(days=18)
    # A portfolio pre-dating the dividend-event feature can already have years
    # of price closes but no dividend coverage. Fetch the complete lookback
    # once before Dual Momentum's USD rules may evaluate it.
    if not first and symbol in ("BIL", "SPY", "ACWX"):
        from .models import DividendEvent
        if not DividendEvent.objects.filter(symbol=symbol).exists():
            since = date(2010, 1, 1)
    state.last_attempt = now
    state.save(update_fields=["last_attempt"])
    try:
        fetched = fetch_chart(symbol, since)
        currency, series = fetched
        usd_closes = {}
        if symbol in usd_holding_symbols():
            if currency != "USD":
                raise ValueError("PRICE_HOLD:RAW_UNIT_MISMATCH")
            sync_ticker(FX_SYMBOL, force=force)
            rates = fx_rates(day for day, _ in series)
            usd_closes = dict(series)
            series = [(day, usd_to_gbp(price, rates[day])) for day, price in series if day in rates]
            if not series:
                raise ValueError("FX_UNAVAILABLE")
            currency = "GBP"
        # Screen the new batch before *any* upsert. A suspicious daily jump
        # (including an unhandled split/restrike or 100x pence error) stays
        # on hold; no later signal may use the older mark as though fresh.
        prior = MarketObservation.objects.filter(symbol=symbol, as_of__lt=series[0][0]).order_by("-as_of").first()
        checked = check_price_series(
            symbol, series, as_of=timezone.localdate(),
            previous=(prior.as_of, prior.price) if prior else None)
        if checked.status != "APPROVED":
            state.last_error = ("PRICE_HOLD:" + checked.code)[:80]
            state.save(update_fields=["last_error"])
            LOG.warning("Market price on hold for %s (%s)", symbol, checked.code)
            return None
        # A revised feed close may indicate a split, wrong units or source
        # restatement. Never silently overwrite accepted history.
        previous_values = dict(MarketObservation.objects.filter(
            symbol=symbol, as_of__in=[day for day, _ in series]
        ).values_list("as_of", "price"))
        revised = [(day, price) for day, price in series
                   if day in previous_values and previous_values[day] != price]
        if revised:
            # Every change is logged. Small ones (a close saved early, a
            # vendor correction) are accepted at once; a big one (a split or
            # unit error) holds the line for the site owner on the Admin page.
            from django.db import transaction
            from .models import PriceRevision
            raw_series = getattr(fetched, "raw_closes", {})
            small = all(abs(price - previous_values[day]) <= previous_values[day] * AUTO_ACCEPT_REVISION
                        for day, price in revised)
            with transaction.atomic():
                for day, price in revised:
                    revision, _ = PriceRevision.objects.get_or_create(
                        symbol=symbol, as_of=day, yahoo_price=price, accepted_at=None,
                        defaults={"stored_price": previous_values[day], "currency": currency,
                                  "yahoo_raw_close": raw_series.get(day)})
                    if small:
                        apply_revision(revision)
            if not small:
                state.last_error = "PRICE_HOLD:REVISED_HISTORICAL_CLOSE"
                state.save(update_fields=["last_error"])
                LOG.warning("Market price revision on hold for %s", symbol)
                return None
            LOG.info("Accepted Yahoo's corrected close for %s", symbol)
        # Capture original quote units only from this vendor response.
        # A patched/legacy two-tuple cannot assert original unit provenance.
        raw_unit = getattr(fetched, "raw_unit", "")
        raw_series = getattr(fetched, "raw_closes", {})
        from .core.catalogue import expected_quote_units
        pinned = expected_quote_units()
        accepted_raw = bool(raw_unit and raw_unit in pinned.get(symbol, ()))
        observations = []
        for day, price in series:
            raw = raw_series.get(day) if accepted_raw else None
            factor = Decimal("100") if raw_unit in ("GBp", "GBX") else Decimal("1")
            justified = bool(
                accepted_raw and isinstance(raw, Decimal) and raw.is_finite()
                and raw > ZERO and
                (raw == usd_closes.get(day) if usd_closes else
                 (raw / factor).quantize(Decimal("0.00000001")) == price)
            )
            observations.append(MarketObservation(
                symbol=symbol, as_of=day, price=price, currency=currency,
                source="yahoo" if justified else "",
                raw_quote_unit=raw_unit if justified else "",
                raw_close=raw if justified else None,
                raw_unit_verified=justified,
            ))
        MarketObservation.objects.bulk_create(
            observations, batch_size=250, ignore_conflicts=True,
        )
        # Cash paid per share (Dual Momentum's 12-month total returns need it).
        # Only for lines kept in their own currency: a converted USD line's
        # dividends would need their own exchange rate, so they're left out.
        from .models import DividendEvent
        for day, paid in (getattr(fetched, "dividends", None) or {}).items():
            if not usd_closes:
                DividendEvent.objects.update_or_create(
                    symbol=symbol, ex_date=day, defaults={"amount": paid, "currency": currency})
        state.last_success = timezone.now()
        state.last_error = ""
        state.save(update_fields=["last_success", "last_error"])
    except Exception as exc:
        # Network errors are surfaced as a status, never replaced with invented data.
        detail = str(exc)
        state.last_error = (detail[:80] if detail.startswith(("PRICE_HOLD:", "FX_UNAVAILABLE"))
                            else type(exc).__name__[:80])
        state.save(update_fields=["last_error"])
        LOG.warning("Yahoo quote unavailable for %s (%s)", symbol, type(exc).__name__)
        return None
    return MarketObservation.objects.filter(symbol=symbol).order_by("-as_of").first()



def settlement_close(symbol, quarter_end, *, days=6):
    """Use last verified daily close on/before quarter end, never later data."""
    return MarketObservation.objects.filter(
        symbol=symbol, as_of__lte=quarter_end,
        as_of__gte=quarter_end - timedelta(days=days),
    ).order_by("-as_of").first()


def _exchange(symbol):
    """The listing venue from a Yahoo symbol: ".L", ".DE", or "" for US lines."""
    return "." + symbol.rsplit(".", 1)[1] if "." in symbol else ""


def period_close(symbol, period_end):
    """The settled close for a review date, or None while it may still change.

    A review is saved once, so it must not use an earlier day's close just
    because the period end's own close hasn't been fetched (or Yahoo skipped
    it). The close counts when it is from the last weekday on or before the
    period end. An earlier close counts only when a later close exists and
    no other line on the same exchange traded on the missing days (so the
    market was shut, e.g. a holiday, rather than the data having a gap).
    A held ticker never counts.
    """
    hold = YahooSync.objects.filter(symbol=symbol, last_error__startswith="PRICE_HOLD").exists()
    close = settlement_close(symbol, period_end)
    if hold or close is None:
        return None
    last_weekday = period_end - timedelta(days=max(0, period_end.weekday() - 4))
    if close.as_of >= last_weekday:
        return close
    if not MarketObservation.objects.filter(symbol=symbol, as_of__gt=period_end).exists():
        return None
    venue = _exchange(symbol)
    missing = MarketObservation.objects.filter(
        as_of__gt=close.as_of, as_of__lte=period_end).exclude(symbol=symbol).exclude(
        symbol__contains="=").values_list("symbol", flat=True)
    if any(_exchange(other) == venue for other in missing):
        return None  # the exchange traded that day: our close is missing, not a holiday
    return close
