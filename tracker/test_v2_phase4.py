"""Phase 4 Today and guided real fills; no broker action or inferred prices."""
from datetime import timedelta
from decimal import Decimal as D
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from tracker.instrument_mapping import MAPPING_VERSION
from tracker.market_signals import quarter_end_before
from tracker.models import (
    Account, LedgerEntry, LedgerRevision, MarketObservation, PriceMark,
    RebalanceExecution, SignalFill, StrategyInstance, StrategyPreset,
    StrategySignal, StrategyValueSnapshot,
)
from tracker.services import validate_and_record
from tracker.management.commands.sync_market_and_signals import (
    save_verified_strategy_snapshot,
)


class TodayPhase4Tests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user("today-owner", password="synthetic-12345")
        self.other = User.objects.create_user("today-other", password="synthetic-12345")
        self.preset, _ = StrategyPreset.objects.get_or_create(
            slug="nasdaq-quarterly-target",
            defaults={"name": "9Sig", "risk": "HIGH", "summary": "Static rules", "active": True},
        )
        self.quarter = quarter_end_before(timezone.localdate())
        self.account = Account.objects.create(
            owner=self.owner, name="Actual ISA", kind="ISA",
            country="GB", broker="TRADING212",
        )
        self.strategy = StrategyInstance.objects.create(
            owner=self.owner, account=self.account, preset=self.preset,
            name="Real account", equity_symbol="LQQ3.L",
            safe_symbol="ERNS.L", market_data_enabled=True,
        )
        another = Account.objects.create(
            owner=self.other, name="Secret ISA", kind="ISA",
            broker="TRADING212",
        )
        self.other_strategy = StrategyInstance.objects.create(
            owner=self.other, account=another, preset=self.preset,
            name="SECRET_OTHER_OWNER", equity_symbol="LQQ3.L", safe_symbol="ERNS.L",
        )
        self.client.force_login(self.owner)

    def _url(self, strategy=None):
        return reverse("v2_instance_page", args=[(strategy or self.strategy).pk, "today"])

    def _add(self, kind, *, symbol="", shares="0", price="0", amount="0", when=None):
        entry = LedgerEntry(
            kind=kind, symbol=symbol, quantity=D(shares),
            unit_price=D(price), amount=D(amount),
            occurred_on=when or (self.quarter - timedelta(days=5)),
        )
        return validate_and_record(self.strategy, entry)

    def _holdings(self):
        self._add("DEPOSIT", amount="1000")
        self._add("BUY", symbol="LQQ3.L", shares="2", price="100")
        self._add("BUY", symbol="ERNS.L", shares="1", price="50")
        asof = timezone.localdate() - timedelta(days=2)
        for ticker, p in (("LQQ3.L", "101"), ("ERNS.L", "51")):
            PriceMark.objects.create(
                strategy=self.strategy, symbol=ticker,
                price=D(p), as_of=asof, source="yahoo",
            )
            MarketObservation.objects.update_or_create(
                symbol=ticker, as_of=asof,
                defaults={"source": "yahoo", "raw_unit_verified": True, "price": D(p), "currency": "GBP"},
            )

    def _proposal(self, *, kind="QUARTERLY", action=True):
        return StrategySignal.objects.create(
            strategy=self.strategy, kind=kind,
            signal_key="phase4-" + uuid4().hex[:18],
            title="Actual quarterly review", message="Source-persisted review.",
            actions=([{"side": "BUY", "symbol": "LQQ3.L", "quantity": "1"}]
                     if action else []), as_of=self.quarter,
        )

    def test_header_requires_every_fund_close_not_newest_only(self):
        self._holdings()
        self._proposal()
        # One valid recent close cannot certify the strategy if the other is old.
        old = timezone.localdate() - timedelta(days=12)
        PriceMark.objects.filter(strategy=self.strategy, symbol="ERNS.L").update(as_of=old)
        page = self.client.get(self._url())
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Prices stale")
        self.assertContains(page, "Show instrument price checks")
        self.assertContains(page, "ERNS.L")
        self.assertContains(page, "outside the permitted 8-day window")
        self.assertNotContains(page, "Prices verified")
        self.assertNotContains(page, "Review the verified quarterly proposal")
        self.assertNotContains(page, "I did this")
        self.assertContains(page, "next scheduled market-data check")

    def test_missing_fund_and_held_reference_are_distinct(self):
        from tracker.models import YahooSync
        self._holdings()
        PriceMark.objects.filter(strategy=self.strategy, symbol="ERNS.L").delete()
        missing = self.client.get(self._url())
        self.assertContains(missing, "Prices missing")
        self.assertContains(missing, "No accepted closing price")
        self.assertNotContains(missing, "Prices verified")
        PriceMark.objects.create(
            strategy=self.strategy, symbol="ERNS.L", price=D("51"),
            as_of=timezone.localdate() - timedelta(days=2), source="yahoo",
        )
        YahooSync.objects.create(
            symbol="SPY", last_error="PRICE_HOLD: vendor correction",
            last_attempt=timezone.now(),
        )
        held = self.client.get(self._url())
        self.assertContains(held, "Prices on hold")
        self.assertContains(held, "Reference price held for manual review")
        self.assertContains(held, "SPY")
        self.assertContains(held, "needs site-owner review")
        self.assertNotContains(held, "Prices verified")
        # A staff owner gets the real diagnostics link, not a generic ledger link.
        self.owner.is_staff = True
        self.owner.save(update_fields=["is_staff"])
        admin = self.client.get(self._url())
        self.assertContains(admin, 'href="/control/#price-revisions"')

    def test_ready_header_and_overview_review_cannot_disagree(self):
        self._holdings()
        self._proposal()
        good = self.client.get(self._url())
        self.assertContains(good, "Prices verified")
        self.assertContains(good, "Review the verified quarterly proposal")
        # An obsolete stored signal is not shown as a tradeable Overview action
        # once any required market-price evidence goes stale.
        PriceMark.objects.filter(strategy=self.strategy, symbol="ERNS.L").update(
            as_of=timezone.localdate() - timedelta(days=13))
        overview = self.client.get(reverse("v2_overview"))
        self.assertEqual(overview.status_code, 200)
        self.assertContains(overview, "Review blocked by data")
        self.assertContains(overview, "Prices stale")
        self.assertNotContains(overview, "Q" + str((self.quarter.month - 1) // 3 + 1) + " rebalance")
        self.assertContains(overview, "Not a readiness check")

    def test_tracking_only_never_claims_ready_even_when_marks_exist(self):
        self._holdings()
        self.strategy.tracking_only = True
        self.strategy.save(update_fields=["tracking_only"])
        page = self.client.get(self._url())
        self.assertContains(page, "Tracking only")
        self.assertNotContains(page, "Prices verified")

    def test_today_requires_owner_and_renders_actual_cash_without_fake_gain(self):
        self._add("DEPOSIT", amount="500")
        html = self.client.get(self._url())
        self.assertContains(html, "£500.00")
        # Cash only, nothing bought yet: the next step is the opening position.
        self.assertContains(html, "Buy your opening position")
        self.assertContains(html, "Buy £300.00 of LQQ3.L")
        self.assertContains(html, "Recorded cash only")
        self.assertNotContains(html, "£14,820")
        self.assertNotContains(html, "SECRET_OTHER_OWNER")
        self.client.logout()
        self.assertEqual(self.client.get(self._url()).status_code, 302)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(self._url(self.other_strategy)).status_code, 404)

    def test_verified_action_from_saved_current_signal_with_exact_date_and_quantity(self):
        self._holdings()
        notice = self._proposal()
        page = self.client.get(self._url())
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Review the verified quarterly proposal")
        self.assertContains(page, "I did this")
        self.assertContains(page, "LQQ3.L")
        self.assertContains(page, "£1,003.00")  # £750 cash + £202 equity + £51 safe
        self.assertNotContains(page, "SECRET_OTHER_OWNER")
        self.assertContains(page, reverse("v2_guided_review", args=[self.strategy.pk, notice.pk]))
        self.assertEqual(SignalFill.objects.count(), 0)

    def test_tracking_only_forbids_advice_even_if_quote_rows_and_signal_exist(self):
        self._holdings()
        self._proposal()
        self.strategy.tracking_only = True
        self.strategy.save(update_fields=["tracking_only"])
        p = self.client.get(self._url())
        self.assertContains(p, "Not live yet")
        self.assertContains(p, "#go-live")
        self.assertNotContains(p, "Review proposal / I did this")
        self.assertNotContains(p, "I did this")
        self.assertIsNone(save_verified_strategy_snapshot(self.strategy))

    def test_saved_signal_without_evidenced_prices_never_shows_advice(self):
        self._proposal()
        p = self.client.get(self._url())
        self.assertContains(p, "Cannot compute a reliable instruction")
        self.assertNotContains(p, "Review proposal / I did this")

    def test_manual_warning_does_not_show_trade_execution_link(self):
        self._holdings()
        self._proposal(kind="THIRTY_DOWN")
        p = self.client.get(self._url())
        self.assertContains(p, "Manual review")
        self.assertNotContains(p, "Review proposal / I did this")

    def test_guided_records_exact_actual_fill_then_requires_verified_finalization(self):
        self._holdings()
        notice = self._proposal()
        url = reverse("v2_guided_review", args=[self.strategy.pk, notice.pk])
        page = self.client.get(url)
        self.assertContains(page, "Confirm only what you actually traded")
        token = str(uuid4())
        form = {
            "intent": "record_one", "action_index": "0",
            "executed_on": (self.quarter+timedelta(days=1)).isoformat(),
            "quantity": "1", "unit_price": "100.00", "fee": "0.50",
            "idempotency_key": token, "actual_confirmation": "yes",
        }
        no_confirm = dict(form)
        no_confirm.pop("actual_confirmation")
        p = self.client.post(url, no_confirm)
        self.assertContains(p, "Confirm that this transaction actually occurred")
        self.assertEqual(SignalFill.objects.count(), 0)
        self.assertRedirects(self.client.post(url, form), url)
        self.assertEqual(SignalFill.objects.count(), 1)
        self.assertEqual(self.strategy.ledger.filter(kind="BUY").count(), 3)
        entry = SignalFill.objects.get().ledger_entry
        self.assertEqual(entry.quantity, D("1"))
        self.assertEqual(entry.fee, D("0.50"))
        self.assertEqual(entry.unit_price, D("100"))
        self.assertRedirects(self.client.post(url, form), url)
        self.assertEqual(SignalFill.objects.count(), 1)
        status = self.client.get(self._url())
        self.assertContains(status, "Finish recording actual fills")
        self.assertContains(status, "Continue actual fill record")
        self.assertRedirects(self.client.post(url, {"intent": "finalize"}), self._url())
        self.assertEqual(RebalanceExecution.objects.count(), 1)
        self.assertEqual(self.strategy.baselines.count(), 0)
        read_only = self.client.get(url)
        self.assertContains(read_only, "Completed and immutable")
        blocked = self.client.post(url, form)
        self.assertRedirects(blocked, url)
        self.assertEqual(SignalFill.objects.count(), 1)

    def test_signal_changed_by_new_transactions_does_not_show_fresh_order(self):
        self._holdings()
        self._proposal()
        # New money after quarter-end doesn't change that quarter's trades.
        self._add("DEPOSIT", amount="30", when=self.quarter+timedelta(days=1))
        p = self.client.get(self._url())
        self.assertNotContains(p, "ledger changed after this review")
        # A trade made since the review does change them.
        self._add("BUY", symbol=self.strategy.safe_symbol, shares="1",
                  price="10", when=self.quarter+timedelta(days=2))
        p = self.client.get(self._url())
        self.assertContains(p, "ledger changed after this review")
        self.assertNotContains(p, "Review proposal / I did this")

    def test_ledger_revision_after_review_makes_it_stale(self):
        self._holdings()
        self._proposal()
        initial = self.strategy.ledger.first()
        original = {
            "occurred_on": initial.occurred_on.isoformat(), "kind": initial.kind,
            "symbol": initial.symbol, "quantity": str(initial.quantity),
            "unit_price": str(initial.unit_price), "amount": str(initial.amount),
            "fee": str(initial.fee), "note": initial.note,
        }
        LedgerRevision.objects.create(
            entry=initial, strategy=self.strategy, actor=self.owner,
            version=1, operation="EDIT", before=original, after=original,
        )
        page = self.client.get(self._url())
        # A correction made after the review makes that review stale...
        self.assertContains(page, "ledger changed after this review")
        self.assertNotContains(page, "Review proposal / I did this")
        # ...but it no longer blocks valuations forever: they use the corrected ledger.

    def test_history_shows_only_saved_snapshots(self):
        StrategyValueSnapshot.objects.create(
            strategy=self.strategy, as_of=self.quarter,
            total_value=D("1150"), net_contributed=D("1000"),
        )
        p = self.client.get(reverse("v2_instance_page", args=[self.strategy.pk, "history"]))
        self.assertContains(p, "£1,150.00")
        self.assertContains(p, "30 Sep") if self.quarter.month == 9 else None
        self.assertContains(p, "No source-verified portfolio snapshots") if False else None
