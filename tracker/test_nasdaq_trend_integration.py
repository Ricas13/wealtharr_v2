"""End-to-end enrolment, daily execution safety and Study regression tests."""
from datetime import date, timedelta
from decimal import Decimal as D
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from tracker.core.catalogue import onboarding_manifest, rows_for, selection_available
from tracker.models import (
    Account, LedgerEntry, MarketObservation, NasdaqTrendState, StrategyInstance,
    StrategyPreset, StrategySignal, StudyPrice, YahooSync,
)
from tracker.services import validate_and_record
from tracker.strategies.nasdaq_trend_dca import plugin, v1
from tracker.strategies.registry import module_for_key


class NasdaqOfferTests(SimpleTestCase):
    def test_normal_strategy_listing_and_plugin(self):
        self.assertTrue(selection_available("nasdaq-trend-dca", "TRADING212", "ISA"))
        self.assertTrue(selection_available("nasdaq-trend-dca", "FIDELITY", "BROKERAGE"))
        self.assertFalse(selection_available("nasdaq-trend-dca", "AJBELL", "ISA"))
        lines = rows_for("nasdaq-trend-dca", "TRADING212", "ISA")
        self.assertEqual({x.role: (x.ticker, x.raw_quote_unit) for x in lines},
                         {"leveraged": ("LQQ3.L", "GBX"),
                          "unleveraged": ("EQQQ.L", "GBX")})
        self.assertEqual({x.weight for x in lines}, {D(0)})
        self.assertIsNotNone(module_for_key("nasdaq-trend-dca").study)



class NasdaqEnrollmentTests(TestCase):
    def test_add_strategy_go_live_and_open_standard_pages(self):
        owner = get_user_model().objects.create_user(
            "trend-enrol", password="long-random-password")
        self.client.force_login(owner)
        add = reverse("v2_add_strategy")
        page = self.client.get(add)
        self.assertContains(page, "3× Nasdaq Trend + DCA")
        posted = self.client.post(add, {
            "strategy": "nasdaq-trend-dca",
            "platform": "TRADING212", "account_type": "ISA",
            "ack": "yes", "mode": "fresh", "name": "Nasdaq Trend",
            "start": (timezone.localdate()-timedelta(days=2)).isoformat(),
            "amount": "1000", "deposited": "yes", "go_live": "yes",
        })
        self.assertEqual(posted.status_code, 302)
        instance = StrategyInstance.objects.get(owner=owner)
        self.assertEqual(instance.strategy_version, v1.VERSION)
        self.assertEqual(set(instance.resolved_instruments["roles"]),
                         {"leveraged", "unleveraged"})
        self.assertFalse(instance.tracking_only)
        self.assertTrue(instance.market_data_enabled)
        for section in ("today", "study", "history", "ledger", "strategy", "settings"):
            with self.subTest(section=section):
                page = self.client.get(reverse(
                    "v2_instance_page", args=[instance.pk, section]))
                self.assertEqual(page.status_code, 200)


class NasdaqDailyReviewTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(
            "trend-owner", password="strong-test-password")
        self.account = Account.objects.create(
            owner=self.owner, name="Trend ISA", kind="ISA",
            country="GB", broker="TRADING212")
        self.preset = StrategyPreset.objects.create(
            slug="nasdaq-trend-dca", name="3x Nasdaq", risk="HIGH")
        self.instance = StrategyInstance.objects.create(
            owner=self.owner, account=self.account, preset=self.preset,
            name="Nasdaq", strategy_key="nasdaq-trend-dca",
            platform_code="TRADING212", account_type_code="ISA",
            started_on=timezone.localdate()-timedelta(days=5),
            strategy_version=v1.VERSION,
            mapping_version=plugin.v1.VERSION and
                onboarding_manifest("nasdaq-trend-dca", "TRADING212", "ISA")["mapping_version"],
            resolved_instruments=onboarding_manifest(
                "nasdaq-trend-dca", "TRADING212", "ISA"),
            owner_checked_on=timezone.localdate(), market_data_enabled=True,
            tracking_only=False,
        )
        self.today = timezone.localdate()
        days = [self.today - timedelta(days=i) for i in range(310, 0, -1)]
        self.days = [d for d in days if d.weekday() < 5]
        self.days = self.days[-205:]
        self.us_end = self.days[-1]
        self.close_date = self.today
        for symbol in ("SPY", "QQQ"):
            rows = [MarketObservation(
                symbol=symbol, as_of=day,
                price=D("90") if symbol == "SPY" and day == self.us_end else D("100"),
                raw_close=D("90") if symbol == "SPY" and day == self.us_end else D("100"),
                currency="USD", source="yahoo",
                raw_quote_unit="USD", raw_unit_verified=True)
                for day in self.days]
            MarketObservation.objects.bulk_create(rows)
        for symbol, price in (("LQQ3.L", D("100")), ("EQQQ.L", D("50"))):
            MarketObservation.objects.create(
                symbol=symbol, as_of=self.close_date,
                price=price, raw_close=price*100, raw_quote_unit="GBp",
                source="yahoo", raw_unit_verified=True, currency="GBP")
        for symbol in ("SPY", "QQQ", "LQQ3.L", "EQQQ.L"):
            YahooSync.objects.create(symbol=symbol, last_success=timezone.now())
        validate_and_record(self.instance, LedgerEntry(
            kind="DEPOSIT", amount=D("1000"),
            occurred_on=self.today - timedelta(days=5)))
        validate_and_record(self.instance, LedgerEntry(
            kind="BUY", symbol="LQQ3.L", quantity=D("5"),
            unit_price=D("100"), occurred_on=self.today - timedelta(days=3)))

    def sync_existing(self, symbol):
        return MarketObservation.objects.filter(symbol=symbol).order_by("-as_of").first()

    def test_daily_spy_exit_generates_one_real_sell_proposal(self):
        NasdaqTrendState.objects.create(
            strategy=self.instance, regime="ON", last_as_of=self.days[-2],
            last_target="LEVERAGED")
        with patch.object(plugin, "sync_ticker", side_effect=self.sync_existing):
            first = plugin.review(self.instance)
            again = plugin.review(self.instance)
        self.assertEqual(first["status"], "ok")
        self.assertEqual(again["status"], "waiting")
        notice = StrategySignal.objects.get(strategy=self.instance)
        self.assertEqual(notice.strategy_version, v1.VERSION)
        self.assertEqual(notice.as_of, self.us_end)
        self.assertEqual([(x["side"], x["symbol"]) for x in notice.actions],
                         [("SELL", "LQQ3.L")])
        state = NasdaqTrendState.objects.get(strategy=self.instance)
        self.assertEqual(state.regime, "OFF")
        self.assertEqual(state.reserve, D(0))  # No fake proceeds before actual fill
        self.assertEqual(StrategySignal.objects.count(), 1)

    def test_qqq_guard_is_not_reset_by_cooling(self):
        state = NasdaqTrendState.objects.create(
            strategy=self.instance, regime="ON", guard="CASH",
            last_as_of=self.days[-2], last_target="CASH")
        # SPY stays inside hysteresis band: still remain in cash.
        MarketObservation.objects.filter(symbol="SPY", as_of=self.us_end).update(
            price=D("100"), raw_close=D("100"))
        with patch.object(plugin, "sync_ticker", side_effect=self.sync_existing):
            self.assertEqual(plugin.review(self.instance)["status"], "ok")
        state.refresh_from_db()
        self.assertEqual(state.guard, "CASH")
        notice = StrategySignal.objects.get(strategy=self.instance)
        self.assertEqual(notice.actions[0]["side"], "SELL")

    def test_missing_spy_session_refuses_to_issue(self):
        MarketObservation.objects.filter(symbol="QQQ", as_of=self.days[-3]).delete()
        with patch.object(plugin, "sync_ticker", side_effect=self.sync_existing):
            response = plugin.review(self.instance)
        self.assertEqual(response["status"], "cannot_compute")
        self.assertFalse(StrategySignal.objects.exists())

    def test_dca_uses_original_cash_reserve_only(self):
        # Previous exit really happened over a month ago and there are no
        # leveraged positions, just confirmed unallocated cash.
        self.instance.ledger.filter(kind="BUY").delete()
        state = NasdaqTrendState.objects.create(
            strategy=self.instance, regime="OFF",
            off_since=self.today - timedelta(days=45),
            last_target="DCA", reserve=D("1200"), reserve_source="confirmed_exit_fills",
            last_as_of=self.days[-2])
        MarketObservation.objects.filter(symbol="SPY", as_of=self.us_end).update(
            price=D("100"), raw_close=D("100"))
        with patch.object(plugin, "sync_ticker", side_effect=self.sync_existing):
            response = plugin.review(self.instance)
        self.assertEqual(response["status"], "ok")
        notice = StrategySignal.objects.get(strategy=self.instance)
        self.assertEqual(notice.actions[0]["symbol"], "EQQQ.L")
        self.assertEqual(notice.actions[0]["side"], "BUY")
        self.assertEqual(notice.actions[0]["dca_installments"], 1)
        self.assertLessEqual(D(notice.actions[0]["estimated_gbp"]), D("100"))
        state.refresh_from_db()
        self.assertEqual(state.completed_instalments, 0)  # No owner-confirmed fill


class NasdaqStudyTests(TestCase):
    def test_real_history_only_and_chart_compatible_signals(self):
        study = plugin.STUDY["GBP"]
        start = date(2024, 1, 2)
        days = [start + timedelta(days=i) for i in range(360)]
        days = [d for d in days if d.weekday() < 5]
        rows = []
        for i, day in enumerate(days):
            for sym, price, currency in (
                ("SPY", 100 + i/10, "USD"),
                ("QQQ", 100 + i/8, "USD"),
                ("VTI", 100 + i/15, "USD"),
                ("GBPUSD=X", 1.25, "USD"),
                ("LQQ3.L", 25 + i/6, "GBP"),
                ("EQQQ.L", 100 + i/7, "GBP"),
            ):
                rows.append(StudyPrice(
                    symbol=sym, as_of=day, close=D(str(price)),
                    adj_close=D(str(price)), currency=currency))
        StudyPrice.objects.bulk_create(rows)
        data = study.load_data()
        self.assertTrue(data.ready)
        self.assertGreaterEqual(len(data.signal_history), 200)
        result = study.run(data, "max", 10000, 0, "next")
        self.assertIsNotNone(result)
        self.assertIn("strategy", result["series"])
        self.assertEqual(result["simulated_notes"], [])
