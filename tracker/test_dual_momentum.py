"""Dual Momentum v1: pure rules, the monthly plug-in loop and its Study."""
from datetime import date, timedelta
from decimal import Decimal as D

from django.test import SimpleTestCase

from tracker.core.calendar import month_end_before, months_before
from tracker.strategies.dual_momentum.v1 import Lookback, Quote, VERSION, choose, evaluate

AS_OF = date(2026, 9, 30)
START = date(2025, 9, 30)
FUNDS = {"us": "U.L", "intl": "I.L", "bonds": "B.L"}


def look(start, end, symbol="X"):
    return Lookback(Quote(symbol, D(start), START), Quote(symbol, D(end), AS_OF))


def lookbacks(us=("100", "115"), intl=("100", "108"), cash=("100", "104")):
    return {"us": look(*us), "intl": look(*intl), "cash": look(*cash)}


QUOTES = {s: Quote(s, D("10"), AS_OF) for s in FUNDS.values()}


class DualMomentumRulesTests(SimpleTestCase):
    def test_the_published_decision(self):
        self.assertEqual(choose(D("0.15"), D("0.08"), D("0.04")), "us")
        self.assertEqual(choose(D("0.15"), D("0.20"), D("0.04")), "intl")
        # US below cash: bonds, even if world ex-US did well.
        self.assertEqual(choose(D("0.03"), D("0.20"), D("0.04")), "bonds")

    def test_switch_sells_everything_else_and_buys_the_chosen_fund_with_all_cash(self):
        result = evaluate(funds=FUNDS, positions={"I.L": D("100")}, cash=D("50"), quotes=QUOTES,
                          lookbacks=lookbacks(), as_of=AS_OF, window_start=START)
        self.assertEqual((result.status, result.reason_code, result.strategy_version),
                         ("action_needed", "SWITCH", VERSION))
        sell, buy = result.trades
        self.assertEqual((sell.symbol, sell.side, sell.quantity), ("I.L", "SELL", D("100")))
        self.assertEqual((buy.symbol, buy.side, buy.quantity), ("U.L", "BUY", D("105")))
        self.assertIn("US shares +15.0%", result.reason)
        self.assertEqual(dict(result.audit)["chosen"], "us")

    def test_already_in_the_chosen_fund_with_no_cash_does_nothing(self):
        result = evaluate(funds=FUNDS, positions={"B.L": D("10")}, cash=D("0.50"), quotes=QUOTES,
                          lookbacks=lookbacks(us=("100", "101")), as_of=AS_OF, window_start=START)
        self.assertEqual((result.status, result.trades), ("ok", ()))
        self.assertIn("hold bonds", result.reason)

    def test_new_cash_goes_into_the_chosen_fund(self):
        result = evaluate(funds=FUNDS, positions={"U.L": D("10")}, cash=D("200"), quotes=QUOTES,
                          lookbacks=lookbacks(), as_of=AS_OF, window_start=START)
        self.assertEqual(result.reason_code, "INVEST")
        self.assertEqual([(t.symbol, t.side, t.quantity) for t in result.trades], [("U.L", "BUY", D("20"))])

    def test_cannot_compute_gives_zero_trades_and_one_reason(self):
        stale = dict(lookbacks())
        stale["cash"] = Lookback(Quote("C", D("100"), START - timedelta(days=20)), Quote("C", D("104"), AS_OF))
        cases = (
            dict(positions={}, cash=D("100"), quotes=QUOTES, lookbacks=stale),
            dict(positions={}, cash=D("100"), quotes=QUOTES, lookbacks={"us": look("1", "2")}),
            dict(positions={"OTHER.L": D("1")}, cash=D("0"), quotes=QUOTES, lookbacks=lookbacks()),
            dict(positions={}, cash=D("-1"), quotes=QUOTES, lookbacks=lookbacks()),
            dict(positions={}, cash=D("100"), quotes={}, lookbacks=lookbacks()),
        )
        for case in cases:
            with self.subTest(case=case):
                result = evaluate(funds=FUNDS, as_of=AS_OF, window_start=START, **case)
                self.assertEqual((result.status, result.trades), ("cannot_compute", ()))
                self.assertTrue(result.reason)

    def test_calendar_helpers(self):
        self.assertEqual(month_end_before(date(2026, 1, 15)), date(2025, 12, 31))
        self.assertEqual(month_end_before(date(2026, 3, 1)), date(2026, 2, 28))
        self.assertEqual(months_before(date(2026, 2, 28), 12), date(2025, 2, 28))
        self.assertEqual(months_before(date(2025, 3, 31), 1), date(2025, 2, 28))


def _with_t212_evidence():
    """Catalogue rows as if Trading 212 evidence were recorded for every Dual Momentum fund."""
    from dataclasses import replace
    from tracker.core import catalogue
    roles = {}
    for key, listings in catalogue.ROLE_LISTINGS.items():
        if key[0] == "dual-momentum":
            first = listings[0]
            roles[key] = (replace(first, brokers={**first.brokers, "TRADING212": "https://example.invalid"}),)
    from unittest.mock import patch
    with patch.dict(catalogue.ROLE_LISTINGS, roles):
        return catalogue._build()


class DualMomentumOfferTests(SimpleTestCase):
    def test_not_offered_without_broker_evidence_and_offered_with_it(self):
        from unittest.mock import patch
        from tracker.core import catalogue
        self.assertFalse(catalogue.selection_available("dual-momentum", "TRADING212", "ISA"))
        with patch.dict(catalogue._CATALOGUE, _with_t212_evidence(), clear=True):
            self.assertTrue(catalogue.selection_available("dual-momentum", "TRADING212", "ISA"))
            rows = catalogue.rows_for("dual-momentum", "TRADING212", "ISA")
            self.assertEqual({r.role: r.isin for r in rows},
                             {"us": "IE00BFMXXD54", "intl": "IE000R4ZNTN3", "bonds": "IE00BG47K971"})
            self.assertTrue(all(r.weight == 0 for r in rows))


from django.test import TestCase  # noqa: E402


class DualMomentumEndToEndTests(TestCase):
    """Setup, opening card, a monthly review, the switch, fills and finish."""

    def setUp(self):
        from unittest.mock import patch
        from django.contrib.auth import get_user_model
        from django.core.cache import cache
        from django.utils import timezone
        from tracker.core import catalogue
        from tracker.models import NotificationPreference
        cache.clear()
        patcher = patch.dict(catalogue._CATALOGUE, _with_t212_evidence(), clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.owner = get_user_model().objects.create_user("dm-owner", password="a-long-test-password-1")
        NotificationPreference.objects.create(owner=self.owner, email_enabled=True,
                                              destination_email="o@example.invalid",
                                              discord_enabled=False, monthly_reminders=False)
        self.client.force_login(self.owner)
        self.today = timezone.localdate()
        self.month_end = month_end_before(self.today)
        self.recent = self.today - timedelta(days=1)
        if self.recent <= self.month_end + timedelta(days=2):
            self.skipTest("Needs a few days after the month end for fills and a later close.")

    def observe(self, day, prices):
        from tracker.models import MarketObservation
        for symbol, price in prices.items():
            MarketObservation.objects.update_or_create(
                symbol=symbol, as_of=day, defaults={"price": D(price), "currency": "GBP"})

    def latest(self, symbol, **kwargs):
        from tracker.models import MarketObservation
        return MarketObservation.objects.filter(symbol=symbol).order_by("-as_of").first()

    def test_monthly_loop(self):
        from unittest.mock import patch
        from uuid import uuid4
        from django.core.management import call_command
        from django.urls import reverse
        from tracker.models import RebalanceExecution, StrategyInstance, StrategySignal
        from tracker.review_autofinish import auto_finish_reviews
        from tracker.strategies.instruments import symbols
        start = self.month_end - timedelta(days=40)
        # A year ago and at the last two month ends: world ex-US leads, US beats cash.
        prior = months_before(month_end_before(start), 12)
        self.observe(prior, {"VUAG.L": "80", "XUSE.L": "20", "VAGS.L": "10", "CSH2.L": "1100"})
        self.observe(months_before(self.month_end, 12),
                     {"VUAG.L": "90", "XUSE.L": "20", "VAGS.L": "10", "CSH2.L": "1150"})
        self.observe(month_end_before(start + timedelta(days=1)),
                     {"VUAG.L": "100", "XUSE.L": "22", "VAGS.L": "10", "CSH2.L": "1140"})
        closes = {"VUAG.L": "100", "XUSE.L": "30", "VAGS.L": "10", "CSH2.L": "1190"}
        self.observe(self.month_end, closes)
        self.observe(self.recent, closes)
        page = self.client.get(reverse("v2_add_strategy") + "?strategy=dual-momentum")
        self.assertContains(page, "By signal")
        self.client.post(reverse("v2_add_strategy"), {
            "strategy": "dual-momentum", "platform": "TRADING212", "account_type": "ISA", "ack": "yes",
            "mode": "fresh", "name": "DM", "start": start.isoformat(), "amount": "3000",
            "deposited": "yes", "go_live": "yes"})
        strategy = StrategyInstance.objects.get(owner=self.owner)
        self.assertEqual(symbols(strategy), ("VUAG.L", "XUSE.L", "VAGS.L"))
        # Opening card: 100% in the fund the latest month end chooses (world ex-US).
        today_page = self.client.get(reverse("v2_instance_page", args=[strategy.pk, "today"]))
        self.assertEqual([row["symbol"] for row in today_page.context["today_opening"]], ["XUSE.L"])
        # Bought US shares instead; the month-end review must switch.
        add = reverse("v2_ledger_add", args=[strategy.pk])
        self.client.post(add, {"intent": "save", "kind": "BUY", "symbol": "VUAG.L", "quantity": "30",
                               "unit_price": "100", "fee": "0", "occurred_on": start.isoformat()})
        with patch("tracker.market_data.sync_ticker", side_effect=self.latest), \
                patch("tracker.management.commands.sync_market_and_signals.sync_ticker"):
            call_command("sync_market_and_signals", verbosity=0)
        signal = StrategySignal.objects.get(strategy=strategy)
        self.assertEqual(signal.as_of, self.month_end)
        self.assertEqual([(a["symbol"], a["side"]) for a in signal.actions],
                         [("VUAG.L", "SELL"), ("XUSE.L", "BUY")])
        self.assertIn("world ex-US", signal.message)
        review = reverse("v2_guided_review", args=[strategy.pk, signal.pk])
        for index, action in enumerate(signal.actions):
            response = self.client.post(review, {
                "intent": "record_one", "action_index": str(index),
                "executed_on": (self.month_end + timedelta(days=2)).isoformat(),
                "quantity": action["quantity"], "unit_price": closes[action["symbol"]], "fee": "0",
                "idempotency_key": str(uuid4()), "actual_confirmation": "yes"})
            self.assertEqual(response.status_code, 302,
                             response.context and response.context.get("review_error"))
        self.assertEqual(auto_finish_reviews(), 1)
        self.assertTrue(RebalanceExecution.objects.filter(signal=signal).exists())
        study = self.client.get(reverse("v2_instance_page", args=[strategy.pk, "study"]))
        self.assertContains(study, "same Dual Momentum rules")


class DualMomentumStudyTests(TestCase):
    def test_monthly_replay_switches_and_flags_stand_ins(self):
        from django.core.cache import cache
        from tracker.strategies.registry import module_for_key
        from tracker.test_study import TODAY, seed
        cache.clear()
        start = TODAY - timedelta(days=1200)
        mid = TODAY - timedelta(days=600)
        seed("GBPUSD=X", start, lambda d, i: 1.25)
        seed("SPY", start, lambda d, i: 400 * (1.0006 ** i) if d < mid else 400 * (1.0006 ** 400) * (0.9995 ** (i - 400)))
        seed("VTI", start, lambda d, i: 200.0)
        seed("VEA", start, lambda d, i: 40 * (1.0002 ** i))
        seed("AGG", start, lambda d, i: 100 * (1.0001 ** i))
        seed("BIL", start, lambda d, i: 90 * (1.00012 ** i))
        seed("VUAG.L", TODAY - timedelta(days=300), lambda d, i: 80.0, currency="GBP")
        seed("XUSE.L", TODAY - timedelta(days=300), lambda d, i: 20.0, currency="GBP")
        seed("VAGS.L", TODAY - timedelta(days=300), lambda d, i: 10.0, currency="GBP")
        seed("CSH2.L", TODAY - timedelta(days=300), lambda d, i: 11.0, currency="GBP")
        study = module_for_key("dual-momentum").study
        data = study.load_data()
        self.assertTrue(data.ready)
        result = study.run(data, "1y", 1000, 0)
        s = result["series"]["strategy"]
        self.assertGreater(s["trades"], 0)
        self.assertTrue(any("VEA" in n for n in result["simulated_notes"]))
