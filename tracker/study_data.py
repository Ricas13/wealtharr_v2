"""Download long daily histories for the hypothetical Study tab.

Yahoo's public chart endpoint, the same one the hourly price check uses, with
dividend-adjusted closes included. Stored in StudyPrice only, so nothing here
can value real holdings or change an instruction.
"""
import json
from datetime import date, datetime, timezone as utc_timezone
from decimal import Decimal
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from django.db import transaction
from django.utils import timezone

from .models import StudyPrice

# Symbol: (expected raw Yahoo currency, stored currency, divisor).
STUDY_SYMBOLS = {
    "QQQ": (("USD",), "USD", Decimal("1")),
    "SPY": (("USD",), "USD", Decimal("1")),
    "VTI": (("USD",), "USD", Decimal("1")),
    # US dollars per pound.
    "GBPUSD=X": (("USD",), "USD", Decimal("1")),
    "LQQ3.L": (("GBp", "GBX"), "GBP", Decimal("100")),
    "ERNS.L": (("GBP",), "GBP", Decimal("1")),
    # Fixed-weight strategies (All Weather, Golden Butterfly): their funds and
    # the stand-ins used before each fund existed.
    "VUAG.L": (("GBP",), "GBP", Decimal("1")),
    "GLTL.L": (("GBP",), "GBP", Decimal("1")),
    "IGLT.L": (("GBP",), "GBP", Decimal("1")),
    "IGLS.L": (("GBP",), "GBP", Decimal("1")),
    "SGLN.L": (("GBp", "GBX"), "GBP", Decimal("100")),
    "CMOP.L": (("GBp", "GBX"), "GBP", Decimal("100")),
    "USSC.L": (("USD",), "USD", Decimal("1")),
    "GLD": (("USD",), "USD", Decimal("1")),
    "DBC": (("USD",), "USD", Decimal("1")),
    "VBR": (("USD",), "USD", Decimal("1")),
    # Dual Momentum: funds, the sterling cash series, and stand-ins.
    "XUSE.L": (("GBP",), "GBP", Decimal("1")),
    "VAGS.L": (("GBP",), "GBP", Decimal("1")),
    "CSH2.L": (("GBp", "GBX"), "GBP", Decimal("100")),
    "VEA": (("USD",), "USD", Decimal("1")),
    "AGG": (("USD",), "USD", Decimal("1")),
    "BIL": (("USD",), "USD", Decimal("1")),
    # Dollar accounts (Fidelity): the original US funds.
    "TLT": (("USD",), "USD", Decimal("1")),
    "IEF": (("USD",), "USD", Decimal("1")),
    "SHY": (("USD",), "USD", Decimal("1")),
    "ACWX": (("USD",), "USD", Decimal("1")),
    "EFA": (("USD",), "USD", Decimal("1")),
    # Euro accounts (Trade Republic): Xetra EUR lines, and US dollars per euro.
    "EURUSD=X": (("USD",), "USD", Decimal("1")),
    "SXR8.DE": (("EUR",), "EUR", Decimal("1")),
    "DBXF.DE": (("EUR",), "EUR", Decimal("1")),
    "SXRQ.DE": (("EUR",), "EUR", Decimal("1")),
    "PPFB.DE": (("EUR",), "EUR", Decimal("1")),
    "SXRS.DE": (("EUR",), "EUR", Decimal("1")),
    "ZPRV.DE": (("EUR",), "EUR", Decimal("1")),
    "DBXP.DE": (("EUR",), "EUR", Decimal("1")),
    "EXUS.DE": (("EUR",), "EUR", Decimal("1")),
    "VAGF.DE": (("EUR",), "EUR", Decimal("1")),
    "XEON.DE": (("EUR",), "EUR", Decimal("1")),
    # 9Sig for dollar (TQQQ, AGG and the TQQQ reference above) and euro accounts.
    "TQQQ": (("USD",), "USD", Decimal("1")),
    "3QQQ.DE": (("EUR",), "EUR", Decimal("1")),
    "ERNX.DE": (("EUR",), "EUR", Decimal("1")),
}
FIRST_DAY = date(2000, 1, 1)
EIGHT = Decimal("0.00000001")


def fetch_study_chart(symbol, since):
    """Daily (date, close, adjusted close) in the stored currency."""
    expected, _, divisor = STUDY_SYMBOLS[symbol]
    params = urlencode({
        "period1": int(datetime.combine(since, datetime.min.time(), utc_timezone.utc).timestamp()),
        "period2": int(timezone.now().timestamp()) + 86400,
        "interval": "1d", "events": "div,split", "includeAdjustedClose": "true",
    })
    chart, last_error = None, None
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        url = "https://%s/v8/finance/chart/%s?%s" % (host, quote(symbol, safe=""), params)
        request = Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; WealtharrOwnerTracker/0.2)",
            "Accept": "application/json",
        })
        try:
            with urlopen(request, timeout=20) as response:
                payload = json.loads(response.read(8_000_000))
            chart = (payload.get("chart", {}).get("result") or [None])[0]
            if chart:
                break
        except (OSError, ValueError) as exc:
            last_error = exc
    if not chart:
        raise last_error or ValueError("Yahoo supplied no chart data")
    if chart.get("meta", {}).get("currency") not in expected:
        raise ValueError("Unexpected quote currency for %s" % symbol)
    indicators = chart.get("indicators") or {}
    closes = (indicators.get("quote") or [{}])[0].get("close") or []
    adjusted = (indicators.get("adjclose") or [{}])[0].get("adjclose") or closes
    rows = {}
    for epoch, close, adj in zip(chart.get("timestamp") or [], closes, adjusted):
        if close is None:
            continue
        day = datetime.fromtimestamp(epoch, tz=utc_timezone.utc).date()
        close = (Decimal(str(close)) / divisor).quantize(EIGHT)
        adj = (Decimal(str(adj if adj is not None else close)) / divisor).quantize(EIGHT)
        if close > 0 and adj > 0 and day <= timezone.localdate():
            rows[day] = (close, adj)
    if not rows:
        raise ValueError("No usable daily closes for %s" % symbol)
    return sorted((day, c, a) for day, (c, a) in rows.items())


def sync_study_prices(*, fetch=fetch_study_chart, force=False):
    """Top up every study series at most once a day. Returns {symbol: status}."""
    results = {}
    today = timezone.localdate()
    for symbol, (_, currency, _) in STUDY_SYMBOLS.items():
        latest = StudyPrice.objects.filter(symbol=symbol).order_by("-as_of").first()
        if latest and not force and timezone.localtime(latest.fetched_at).date() >= today:
            results[symbol] = "fresh"
            continue
        # Always a full download: adjusted closes change after every dividend.
        try:
            rows = fetch(symbol, FIRST_DAY)
        except Exception as exc:  # A study download never blocks anything else.
            results[symbol] = "failed: %s" % type(exc).__name__
            continue
        with transaction.atomic():
            StudyPrice.objects.filter(symbol=symbol).delete()
            StudyPrice.objects.bulk_create([
                StudyPrice(symbol=symbol, as_of=day, close=close, adj_close=adj, currency=currency)
                for day, close, adj in rows
            ], batch_size=2000)
        results[symbol] = "%d days" % len(rows)
    return results
