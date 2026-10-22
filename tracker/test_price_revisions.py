"""Live session bars are never stored as closes; a changed close is reviewed and accepted on the Admin page."""
import io
import json
from datetime import date, datetime, timedelta, timezone as utc
from decimal import Decimal as D
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from tracker.market_data import fetch_chart, sync_ticker
from tracker.models import MarketObservation, PriceRevision, YahooSync


def _chart(days, closes, regular=None, currency="USD"):
    stamps = [int(datetime.combine(d, datetime.min.time(), utc.utc).timestamp()) + 13 * 3600 + 1800 for d in days]
    meta = {"currency": currency}
    if regular:
        meta["currentTradingPeriod"] = {"regular": {"start": regular[0], "end": regular[1]}}
    return {"chart": {"result": [{"meta": meta, "timestamp": stamps,
                                  "indicators": {"quote": [{"close": closes}]}}]}}


class _Reply(io.BytesIO):
    status = 200


def fetch(payload):
    with patch("tracker.market_data.urlopen", return_value=_Reply(json.dumps(payload).encode())):
        return fetch_chart("SPY", date(2026, 10, 1))


class LiveBarTests(TestCase):
    def setUp(self):
        self.today = datetime.now(utc.utc).date()
        self.days = [self.today - timedelta(days=1), self.today]
        self.open_ts = int(datetime.combine(self.today, datetime.min.time(), utc.utc).timestamp()) + 13 * 3600 + 1800

    def test_todays_bar_is_left_out_while_the_session_is_open(self):
        now = timezone.now().timestamp()
        series = fetch(_chart(self.days, [700, 772.84], regular=(self.open_ts, now + 3600))).series
        self.assertEqual([d for d, _ in series], [self.days[0]])

    def test_todays_bar_counts_once_the_session_has_settled(self):
        now = timezone.now().timestamp()
        series = fetch(_chart(self.days, [700, 771], regular=(self.open_ts, now - 3600))).series
        self.assertEqual([d for d, _ in series], self.days)

    def test_without_session_times_todays_bar_is_left_out(self):
        series = fetch(_chart(self.days, [700, 771])).series
        self.assertEqual([d for d, _ in series], [self.days[0]])


class PriceRevisionTests(TestCase):
    def setUp(self):
        self.day = timezone.localdate() - timedelta(days=1)
        MarketObservation.objects.create(symbol="SPY", as_of=self.day, price=D("772.84"), currency="USD",
                                         source="yahoo", raw_quote_unit="USD", raw_close=D("772.84"),
                                         raw_unit_verified=True)
        self.staff = get_user_model().objects.create_user("boss", password="a-long-test-password-1",
                                                          is_staff=True, is_superuser=True)

    def hold(self):
        with patch("tracker.market_data.fetch_chart", return_value=("USD", [(self.day, D("770.10"))])):
            self.assertIsNone(sync_ticker("SPY", force=True))

    def test_a_changed_close_holds_the_line_and_is_listed_for_review(self):
        self.hold()
        self.hold()  # the hourly re-check doesn't duplicate it
        self.assertEqual(YahooSync.objects.get(symbol="SPY").last_error, "PRICE_HOLD:REVISED_HISTORICAL_CLOSE")
        revision = PriceRevision.objects.get()
        self.assertEqual((revision.stored_price, revision.yahoo_price), (D("772.84"), D("770.10")))
        self.assertEqual(MarketObservation.objects.get(symbol="SPY").price, D("772.84"))
        self.client.force_login(self.staff)
        page = self.client.get(reverse("control"))
        self.assertContains(page, "Price corrections waiting for you")
        self.assertContains(page, "Accept Yahoo")

    def test_accepting_corrects_the_close_records_who_and_releases_the_hold(self):
        self.hold()
        self.client.force_login(self.staff)
        response = self.client.post(reverse("accept_price_revision", args=["SPY"]))
        self.assertRedirects(response, reverse("control"))
        observation = MarketObservation.objects.get(symbol="SPY")
        self.assertEqual((observation.price, observation.raw_close), (D("770.10"), D("770.10")))
        revision = PriceRevision.objects.get()
        self.assertEqual(revision.accepted_by, self.staff)
        self.assertEqual(revision.stored_price, D("772.84"))  # the old value stays on record
        self.assertEqual(YahooSync.objects.get(symbol="SPY").last_error, "")
        self.assertNotContains(self.client.get(reverse("control")), "Price corrections waiting for you")

    def test_only_staff_can_accept(self):
        self.hold()
        user = get_user_model().objects.create_user("owner", password="a-long-test-password-1")
        self.client.force_login(user)
        self.client.post(reverse("accept_price_revision", args=["SPY"]))
        self.assertIsNone(PriceRevision.objects.get().accepted_at)
        self.assertEqual(MarketObservation.objects.get(symbol="SPY").price, D("772.84"))
