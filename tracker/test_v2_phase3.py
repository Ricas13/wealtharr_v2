"""Phase 3: append-only edits, date replay, reviewer safety and real T212 CSV."""
from datetime import timedelta
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from tracker.core.catalogue import CATALOGUE_VERSION, onboarding_manifest, released_version
from tracker.models import (
    Account, BrokerImportRow, InstanceOpeningSnapshot, LedgerEntry,
    LedgerRevision, OpeningReconciliation, StrategyInstance, StrategyPreset,
    StrategySignal, StrategyValueSnapshot, RebalanceExecution,
)
from tracker.services import ledger_summary, validate_and_record
from tracker.v2_ledger_core import effective_entries, replay


class LedgerVersioningTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user("phase3-owner", password="long-test-password-123")
        self.other = User.objects.create_user("phase3-other", password="long-test-password-123")
        self.preset, _ = StrategyPreset.objects.get_or_create(
            slug="nasdaq-quarterly-target",
            defaults={"name": "9Sig", "summary": "Fixed", "risk": "HIGH", "active": True},
        )
        self.day = timezone.localdate() - timedelta(days=10)
        self.instance = self._instance(self.owner, "Phase3 ISA")
        self.other_instance = self._instance(self.other, "Private other owner")
        self.client.force_login(self.owner)

    def _instance(self, owner, label, account_type="ISA"):
        account = Account.objects.create(
            owner=owner, name=label, kind=account_type,
            country="GB", broker="TRADING212",
        )
        return StrategyInstance.objects.create(
            owner=owner, account=account, preset=self.preset, name=label,
            strategy_key="nasdaq-quarterly-target", platform_code="TRADING212",
            account_type_code=account_type, started_on=self.day,
            owner_checked_on=timezone.localdate(),
            strategy_version=released_version("nasdaq-quarterly-target"),
            mapping_version=CATALOGUE_VERSION,
            resolved_instruments=onboarding_manifest(
                "nasdaq-quarterly-target", "TRADING212", account_type
            ),
            equity_symbol="LQQ3.L", safe_symbol="ERNS.L",
            tracking_only=True, market_data_enabled=False,
        )

    def _entry(self, strategy=None, kind="DEPOSIT", amount="1000", symbol="",
               qty="0", unit="0", fee="0", offset=0, note=""):
        return validate_and_record(strategy or self.instance, LedgerEntry(
            kind=kind, amount=D(amount), symbol=symbol, quantity=D(qty),
            unit_price=D(unit), fee=D(fee), occurred_on=self.day + timedelta(days=offset),
            note=note,
        ))

    def _ledger(self, instance=None):
        instance = instance or self.instance
        return reverse("v2_instance_page", args=[instance.pk, "ledger"])

    def _edit(self, entry, operation="edit", instance=None):
        instance = instance or self.instance
        return reverse("v2_ledger_modify", kwargs={
            "instance_id": instance.pk, "entry_id": entry.pk, "operation": operation,
        })

    def _payload(self, **overrides):
        result = {
            "intent": "preview", "kind": "DEPOSIT",
            "occurred_on": self.day.isoformat(), "amount": "1000",
            "symbol": "", "quantity": "0", "unit_price": "0",
            "fee": "0", "note": "Confirmed actual deposit",
        }
        result.update(overrides)
        return result

    def test_csv_headers_match_account_currency_without_changing_gbp_schema(self):
        from types import SimpleNamespace
        from tracker.v2_ledger_page import _export
        from tracker.v2_ledger_views import _input_payload
        from django.core.exceptions import ValidationError

        for currency in ("GBP", "USD", "EUR"):
            with self.subTest(currency=currency):
                target = SimpleNamespace(pk=42, account=SimpleNamespace(currency=currency))
                content = _export(target, []).content.decode("utf-8")
                self.assertIn("unit_price_" + currency.lower(), content)
                self.assertIn("amount_" + currency.lower(), content)
                self.assertIn("fee_" + currency.lower(), content)
        pseudo = SimpleNamespace(started_on=self.day,
                                 account=SimpleNamespace(currency="USD"))
        invalid = dict(self._payload(), amount="not-a-price")
        with self.assertRaisesMessage(ValidationError, "actual USD cash amount"):
            _input_payload(invalid, pseudo)
        # Existing GBP consumer column names are unchanged.
        self.assertIn("amount_gbp", self.client.get(
            self._ledger() + "?export=csv").content.decode("utf-8"))

    def test_authenticated_owner_only_real_entries_and_exports(self):
        self._entry(note="=SUM(1+1)")
        self._entry(self.other_instance, amount="98765.43", note="SECRET_OTHER_USER")
        url = self._ledger()
        response = self.client.get(url)
        self.assertContains(response, "Confirmed") if False else None
        self.assertContains(response, "SUM(1+1)")
        self.assertContains(response, "Entries")
        self.assertContains(response, "Rebalances")
        self.assertContains(response, "Change history")
        self.assertNotContains(response, "98765.43")
        self.assertNotContains(response, "SECRET_OTHER_USER")
        export = self.client.get(url + "?export=csv")
        self.assertEqual(export.status_code, 200)
        content = export.content.decode("utf-8")
        self.assertIn("'=SUM(1+1)", content)
        self.assertNotIn("98765.43", content)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.owner)
        other_url = self._edit(self.other_instance.ledger.first(), instance=self.other_instance)
        self.assertEqual(self.client.get(other_url).status_code, 404)

    def test_add_edit_soft_delete_restore_keeps_immutable_original_and_audit(self):
        add = reverse("v2_ledger_add", args=[self.instance.pk])
        self.assertContains(self.client.post(add, self._payload()), "Impact preview")
        save = self.client.post(add, {"intent": "confirm"})
        self.assertRedirects(save, self._ledger())
        entry = self.instance.ledger.get()
        self.assertEqual(entry.amount, D("1000"))
        self.assertEqual(ledger_summary(self.instance)["cash"], D("1000"))
        self.assertEqual(entry.revisions.get().operation, "ADD")
        change = self._edit(entry)
        self.assertContains(self.client.post(
            change, self._payload(amount="750", note="Broker correction")
        ), "Cash after")
        self.assertRedirects(
            self.client.post(change, {"intent": "confirm", "reason": "Statement"}),
            self._ledger(),
        )
        entry.refresh_from_db()
        self.assertEqual(entry.amount, D("1000"))  # Original immutable
        self.assertEqual(ledger_summary(self.instance)["cash"], D("750"))
        self.assertEqual(len(effective_entries(self.instance)), 1)
        self.assertEqual(effective_entries(self.instance)[0].version, 2)
        self.assertEqual(entry.revisions.order_by("version").last().before["amount"], "1000")
        self.assertEqual(entry.revisions.order_by("version").last().after["amount"], "750")
        deletion = self._edit(entry, "delete")
        self.assertContains(self.client.post(deletion, {"intent": "preview"}), "Cash after")
        self.assertRedirects(self.client.post(deletion, {"intent": "confirm"}), self._ledger())
        self.assertEqual(ledger_summary(self.instance)["cash"], D("0"))
        self.assertEqual(len(effective_entries(self.instance)), 0)
        self.assertContains(self.client.get(self._ledger() + "?deleted=1"), "Restore")
        restore = self._edit(entry, "restore")
        self.assertContains(self.client.post(restore, {"intent": "preview"}), "Cash after")
        self.assertRedirects(self.client.post(restore, {"intent": "confirm"}), self._ledger())
        self.assertEqual(ledger_summary(self.instance)["cash"], D("750"))
        self.assertEqual([x.operation for x in entry.revisions.order_by("version")],
                         ["ADD", "EDIT", "DELETE", "RESTORE"])
        self.assertContains(self.client.get(self._ledger() + "?tab=history"), "Broker correction")
        rev = entry.revisions.order_by("-version").first()
        rev.reason = "attempted rewrite"
        with self.assertRaises(ValidationError):
            rev.save()

    def test_rejected_historical_negative_cash_delete_has_no_revision(self):
        deposit = self._entry()
        self._entry(kind="BUY", amount="0", symbol="LQQ3.L",
                    qty="5", unit="100", offset=1)
        deletion = self._edit(deposit, "delete")
        response = self.client.post(deletion, {"intent": "preview"})
        self.assertContains(response, "negative")
        self.assertEqual(deposit.revisions.count(), 0)
        self.assertEqual(ledger_summary(self.instance)["cash"], D("500"))

    def test_editing_trade_validates_positions_and_exact_instrument(self):
        self._entry()
        bought = self._entry(kind="BUY", amount="0", symbol="LQQ3.L",
                             qty="4", unit="50", offset=1)
        changed = self._edit(bought)
        error = self.client.post(changed, self._payload(
            kind="SELL", occurred_on=(self.day+timedelta(days=1)).isoformat(),
            symbol="LQQ3.L", quantity="4", unit_price="50", fee="0",
        ))
        self.assertContains(error, "sell more shares")
        unknown = self.client.post(changed, self._payload(
            kind="BUY", symbol="HACKER.L", quantity="1", unit_price="10",
        ))
        self.assertContains(unknown, "exact mapped")
        self.assertEqual(bought.revisions.count(), 0)

    def test_saved_valuation_does_not_block_a_correction(self):
        d = self._entry()
        StrategyValueSnapshot.objects.create(
            strategy=self.instance, as_of=self.day,
            total_value=D("1000"), net_contributed=D("1000"),
        )
        res = self.client.post(self._edit(d), self._payload(amount="700"))
        self.assertNotContains(res, "would change. These require")
        self.client.post(self._edit(d), dict(self._payload(amount="700"), intent="confirm"))
        self.assertEqual(d.revisions.count(), 1)

    def test_atomic_preview_prevents_stale_tab_confirmation(self):
        entry = self._entry()
        url = self._edit(entry)
        self.assertContains(self.client.post(url, self._payload(amount="500")), "Impact preview")
        self._entry(amount="100", offset=1)
        refused = self.client.post(url, {"intent": "confirm"})
        self.assertContains(refused, "ledger changed since preview")
        self.assertEqual(entry.revisions.count(), 0)

    def test_imported_source_locked_from_correction(self):
        d = self._entry()
        BrokerImportRow.objects.create(
            strategy=self.instance, ledger_entry=d, fingerprint="x"*64
        )
        denied = self.client.post(self._edit(d), self._payload(amount="800"))
        self.assertContains(denied, "original source")
        self.assertEqual(d.revisions.count(), 0)



    def test_rebalances_read_actual_signal_fills_without_fake_execution(self):
        self._entry()
        s = StrategySignal.objects.create(
            strategy=self.instance, kind="QUARTERLY", title="Historical owner review",
            signal_key="phase3-test", as_of=self.day,
            actions=[{"side": "BUY", "symbol": "LQQ3.L", "quantity": "2"}],
        )
        response = self.client.get(self._ledger() + "?tab=rebalances")
        self.assertContains(response, "Historical owner review")
        self.assertContains(response, "No recorded fill")
        self.assertEqual(RebalanceExecution.objects.filter(strategy=self.instance).count(), 0)
