"""Fixes from the October 2026 outside review: flexible completion and one XIRR source."""
from datetime import timedelta
from decimal import Decimal as D

from tracker.cockpit_execution import completion_rows
from tracker.models import SignalFill
from tracker.test_g_single_fill import ReviewFixture


class FlexibleCompletionTests(ReviewFixture):
    """The fixture's review: SELL 1 LQQ3.L (~£100), BUY 2 ERNS.L (~£50)."""

    def sell_first(self):
        self.client.post(self.url, self.fill(0, "1", "100"))
        self.assertTrue(self.rows()[0]["complete"])

    def rows(self):
        self.signal.refresh_from_db()
        fills = list(SignalFill.objects.filter(signal=self.signal).select_related("ledger_entry"))
        return completion_rows(self.signal, fills)

    def test_a_fill_within_two_percent_counts_as_done(self):
        self.sell_first()
        self.client.post(self.url, self.fill(1, "1.97", "50"))
        self.assertTrue(self.rows()[1]["complete"])

    def test_up_to_two_percent_over_is_accepted_but_not_more(self):
        self.sell_first()
        self.client.post(self.url, self.fill(1, "2.05", "48"))
        self.assertEqual(SignalFill.objects.filter(signal=self.signal).count(), 1)
        self.client.post(self.url, self.fill(1, "2.03", "48"))
        self.assertTrue(self.rows()[1]["complete"])

    def test_a_different_amount_can_be_accepted_as_done_and_is_noted(self):
        self.sell_first()
        self.client.post(self.url, self.fill(1, "1.5", "50"))
        self.assertFalse(self.rows()[1]["complete"])
        page = self.client.get(self.url)
        self.assertContains(page, "Mark this trade done")
        self.client.post(self.url, {"intent": "accept_action", "action_index": "1", "note": "Ran out of cash"})
        row = self.rows()[1]
        self.assertTrue(row["complete"])
        self.assertEqual(D(row["accepted"]["recorded"]), D("1.5"))
        self.assertEqual(row["accepted"]["note"], "Ran out of cash")
        # No more fills on an accepted trade; the ledger keeps only the real fill.
        self.client.post(self.url, self.fill(1, "0.4", "50"))
        self.assertEqual(SignalFill.objects.filter(signal=self.signal).count(), 2)

    def test_a_trade_that_was_not_made_can_be_accepted_without_a_fill(self):
        self.client.post(self.url, {"intent": "accept_action", "action_index": "0"})
        self.assertTrue(self.rows()[0]["complete"])
        self.assertEqual(self.rows()[0]["recorded"], D("0"))

    def test_another_owner_cannot_accept(self):
        self.client.force_login(self.other)
        response = self.client.post(self.url, {"intent": "accept_action", "action_index": "0"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.signal.__class__.objects.get(pk=self.signal.pk).accepted_actions, {})


class OneXirrSourceTests(ReviewFixture):
    def test_the_dashboard_xirr_needs_a_certified_valuation(self):
        from tracker import v2_dashboard as dash
        from tracker.models import StrategyValueSnapshot
        # A saved snapshot alone (no source-checked close behind it) gives no XIRR.
        StrategyValueSnapshot.objects.create(strategy=self.strategy, as_of=self.quote_day,
                                             total_value=D("1500"), net_contributed=D("1000"))
        self.strategy.ledger.filter(kind="DEPOSIT").update(occurred_on=self.quote_day - timedelta(days=200))
        self.assertIsNone(dash.xirr_pct(self.strategy))
