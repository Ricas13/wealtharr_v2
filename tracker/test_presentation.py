"""Section E: presentation only; no strategy-calculation or broker side effects."""
from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace
from unittest import TestCase

from tracker.core.presentation import (
    account_value, holding_rows, ledger_cash_movement, ledger_display_rows,
    select_next_step, signal_display_rows, visible_performance,
)


class NextStepPresentationTests(TestCase):
    def advice(self, kind="", actions=(), ready=True, reason=""):
        return select_next_step(
            signal_kind=kind, signal_title="Owner warning",
            signal_message="Review reference before action",
            proposed_actions=tuple(actions), price_ready=ready,
            unavailable_reason=reason, next_review_label="31 Dec 2026")

    def test_alert_is_manual_review_even_when_quotes_are_missing(self):
        step = self.advice(kind="SPIKE_WATCH", actions=(
            {"side": "BUY", "symbol": "X"},), ready=False, reason="Missing quote")
        self.assertEqual(step.state, "review")
        self.assertEqual(step.details, "Missing quote")
        self.assertNotIn("Buy", step.title)

    def test_unverified_signal_cannot_be_executable(self):
        step = self.advice(kind="QUARTERLY", actions=(
            {"side": "BUY", "symbol": "X"},), ready=False,
            reason="Outdated quarter")
        self.assertEqual(step.state, "blocked")
        self.assertIn("Outdated quarter", step.explanation)

    def test_verified_quarterly_trade_and_no_action_states(self):
        self.assertEqual(self.advice(kind="QUARTERLY", actions=(
            {"side": "SELL", "symbol": "X"},)).state, "trade")
        self.assertEqual(self.advice(kind="QUARTERLY", actions=(
            {"side": "HOLD", "symbol": "X"},)).state, "idle")
        self.assertEqual(self.advice().state, "idle")

    def test_cash_only_value_is_actual_balance_not_fabricated_quote(self):
        self.assertEqual(account_value(net_cash=D("1000"),
            portfolio_value=D("1000"), prices_verified=False,
            has_holdings=False), D("1000"))
        self.assertIsNone(account_value(net_cash=D("700"),
            portfolio_value=D("1100"), prices_verified=False,
            has_holdings=True))

    def test_return_zero_is_available_not_missing(self):
        displayed = visible_performance({"xirr": D("0"),
                                         "twr": None, "drawdown": None})
        self.assertTrue(displayed[0]["available"])
        self.assertFalse(displayed[1]["available"])

    def test_no_quotes_means_unpriced_holdings_not_assumed_value(self):
        report = {"positions": {"A": D("2"), "B": D("0")},
                  "marks": {"A": SimpleNamespace(price=D("10"),
                                                  as_of=date(2026, 3, 31))}}
        rows = holding_rows(report, roles={"A": "Growth"}, show_quotes=False)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].role, "Growth")
        self.assertIsNone(rows[0].value_gbp)
        self.assertEqual(holding_rows(report, roles={}, show_quotes=True)[0].value_gbp,
                         D("20"))


class LedgerPresentationTests(TestCase):
    def entry(self, kind, amount=0, qty=0, price=0, fee=0, identity=1):
        return SimpleNamespace(
            kind=kind, amount=D(str(amount)), quantity=D(str(qty)),
            unit_price=D(str(price)), fee=D(str(fee)), symbol="GROWTH",
            occurred_on=date(2026, 3, 31), note="Actual event", pk=identity,
            get_kind_display=lambda: kind.title())

    def test_display_matches_ledger_money_rules(self):
        expected = [
            (self.entry("DEPOSIT", amount="1000", identity=1), D("1000")),
            (self.entry("WITHDRAW", amount="35", identity=2), D("-35")),
            (self.entry("DIVIDEND", amount="2.50", identity=3), D("2.50")),
            (self.entry("FEE", amount="1", identity=4), D("-1")),
            (self.entry("BUY", qty="1.5", price="100", fee="1.25", identity=5), D("-151.25")),
            (self.entry("SELL", qty="1", price="110", fee="0.50", identity=6), D("109.50")),
        ]
        for entry, cash_movement in expected:
            self.assertEqual(ledger_cash_movement(entry), cash_movement)
        rows = ledger_display_rows([entry for entry, _ in expected])
        self.assertEqual(rows[4]["cash_change"], D("-151.25"))
        self.assertEqual(rows[0]["quantity"], None)
        self.assertEqual(rows[5]["id"], 6)

    def test_historical_alerts_display_as_records_not_live_orders(self):
        records = signal_display_rows([
            SimpleNamespace(pk=12, as_of=date(2026, 3, 31),
                title="Old proposal", message="Recorded review",
                kind="QUARTERLY", review_outcome="",
                acknowledged_at=None,
                actions=[{"side": "BUY", "symbol": "GROWTH"}],
                get_review_outcome_display=lambda: "")])
        self.assertTrue(records[0]["has_proposals"])
        self.assertEqual(records[0]["outcome"], "Not reviewed")
        self.assertNotIn("action_url", records[0])
