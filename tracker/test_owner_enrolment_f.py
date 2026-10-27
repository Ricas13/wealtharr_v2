"""Owner-only ISA enrolment, real contributions, and Trading 212 CSV gates in v2.

The retired one-page cockpit setup is gone. These tests exercise the released
five-step wizard and audited v2 Ledger, preserving the no-invented-cash rules.
"""
from datetime import date, timedelta
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from tracker.core.catalogue import (
    CATALOGUE_VERSION, onboarding_manifest, released_version,
)
from tracker.instrument_mapping import enrolment_manifest, InstrumentMappingUnavailable
from tracker.v2_onboarding import OPENING_NOTE
from tracker.models import (
    Account, BrokerImportRow, InstanceOpeningSnapshot, LedgerEntry,
    StrategyInstance, StrategyPreset,
)


class OwnerEnrollmentTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(
            username="section_f_owner", password="strong-example-test-password")
        self.client.force_login(self.owner)
        self.preset, _ = StrategyPreset.objects.get_or_create(
            slug="nasdaq-quarterly-target",
            defaults={"name": "9Sig", "summary": "", "risk": "HIGH",
                      "methodology": "", "active": True})
        self.today = timezone.localdate()
        self.url = reverse("v2_add_strategy")

    BASE = {"strategy": "nasdaq-quarterly-target", "platform": "TRADING212",
            "account_type": "ISA", "ack": "yes", "ack_choice": "yes"}

    def create(self, data):
        return self.client.post(self.url, dict(self.BASE, **data))

    def finish(self, *, mode="fresh", date_at=None, amount="1000",
               name="Owner 9Sig ISA", **holdings):
        data = {
            "mode": mode, "name": name,
            "start": (date_at or self.today-timedelta(days=4)).isoformat(),
        }
        if mode == "fresh":
            data.update(amount=amount, deposited="yes")
        else:
            data.update(cash="0", shares_growth="0", cost_growth="0",
                        shares_safe="0", cost_safe="0")
            data.update(holdings)
        done = self.create(data)
        instance = StrategyInstance.objects.get(owner=self.owner, name=name)
        self.assertRedirects(
            done, reverse("v2_instance_page", args=[instance.pk, "today"]))
        return instance

    def test_one_screen_fixed_isa_scope_and_named_issuer_evidence(self):
        page = self.client.get(self.url)
        self.assertContains(page, "IE00BLRPRL42")
        self.assertContains(page, "IE00BCRY6441")
        self.assertContains(page, "Trading 212")
        self.assertNotContains(page, 'name="equity_symbol"')
        self.assertNotContains(page, 'name="safe_symbol"')

    def test_opening_cash_is_recorded_as_a_labelled_opening_position(self):
        # What the owner holds at the start becomes a clearly labelled opening position.
        s = self.finish(mode="hold", cash="250")
        self.assertEqual(s.account.kind, "ISA")
        self.assertEqual(s.account.country, "GB")
        self.assertEqual(s.account.broker, "TRADING212")
        self.assertEqual(s.account.currency, "GBP")
        opening = s.ledger.get()
        self.assertEqual((opening.kind, opening.amount, opening.note), ("DEPOSIT", D("250.00"), OPENING_NOTE))
        self.assertEqual(s.mapping_version, CATALOGUE_VERSION)
        self.assertEqual(s.equity_symbol, "LQQ3.L")
        self.assertEqual(s.safe_symbol, "ERNS.L")
        self.assertTrue(s.strategy_version.startswith("nine_sig."))
        self.assertEqual(s.resolved_instruments["roles"]["growth"]["isin"], "IE00BLRPRL42")
        self.assertEqual(s.resolved_instruments["roles"]["safe"]["isin"], "IE00BCRY6441")
        self.assertEqual(s.resolved_instruments["roles"]["growth"]["raw_quote_unit"], "GBX")
        self.assertFalse(s.resolved_instruments["roles"]["growth"]["broker_isa_confirmed"])
        self.assertTrue(s.tracking_only)

    def test_fresh_owner_provided_cash_records_exact_date_once(self):
        actual_date = self.today - timedelta(days=4)
        s = self.finish(date_at=actual_date, amount="1000.00")
        entry = s.ledger.get()
        self.assertEqual(entry.kind, "DEPOSIT")
        self.assertEqual(entry.amount, D("1000"))
        self.assertEqual(entry.occurred_on, actual_date)
        # Refreshing a completed wizard does not replay actual funding.
        self.client.get(reverse("v2_instance_page", args=[s.pk, "today"]))
        self.assertEqual(s.ledger.count(), 1)

    def test_existing_holdings_become_the_opening_position(self):
        s = self.finish(mode="hold", cash="50", shares_growth="2", cost_growth="100")
        self.assertEqual(
            sorted(s.ledger.values_list("kind", "symbol", "quantity", "amount")),
            [("BUY", "LQQ3.L", D("2"), D("0")), ("DEPOSIT", "", D("0"), D("250.00"))])
        self.assertFalse(InstanceOpeningSnapshot.objects.filter(strategy=s).exists())

    def test_existing_holdings_reject_double_counted_opening_cash(self):
        response = self.create({
            "mode": "hold", "name": "No fictitious cost",
            "start": self.today.isoformat(), "cash": "500",
            "shares_growth": "0", "cost_growth": "100",
        })
        self.assertContains(response, "price when you hold no growth shares")
        self.assertFalse(StrategyInstance.objects.filter(owner=self.owner).exists())
        self.assertFalse(Account.objects.filter(owner=self.owner).exists())

    def test_fresh_requires_actual_date_for_recorded_cash(self):
        response = self.create({
            "mode": "fresh", "name": "Missing date",
            "start": "", "amount": "1000", "deposited": "yes",
        })
        self.assertContains(response, "valid start date")
        self.assertFalse(StrategyInstance.objects.filter(owner=self.owner).exists())

    def test_cannot_backfill_future_cash_and_gia_request_is_not_offered(self):
        future = self.today + timedelta(days=1)
        invalid = self.create({
            "mode": "fresh", "name": "Future not real",
            "start": future.isoformat(), "amount": "50", "deposited": "yes",
        })
        self.assertContains(invalid, "future")
        self.assertFalse(Account.objects.filter(owner=self.owner).exists())
        # Forged old account_kind is ignored after explicit ISA wizard selection.
        valid = self.create({
            "mode": "fresh", "name": "Real account",
            "start": self.today.isoformat(), "amount": "50",
            "deposited": "yes", "account_kind": "GIA",
        })
        self.assertEqual(valid.status_code, 302)
        self.assertEqual(StrategyInstance.objects.get(owner=self.owner).account.kind, "ISA")

    def test_optional_notifications_and_monthly_zero_remain_planning(self):
        s = self.finish(mode="hold", cash="50")
        self.assertEqual(s.monthly_target, D("0"))
        self.assertEqual(s.ledger.get().note, OPENING_NOTE)
        self.assertTrue(s.alert_routes["quarterly"]["email"])
        self.assertFalse(s.alert_routes["contribution"]["email"])

    def test_owner_can_record_actual_contribution_after_enrolling(self):
        s = self.finish(date_at=self.today-timedelta(days=5), amount="100")
        day = self.today-timedelta(days=2)
        url = reverse("v2_ledger_add", args=[s.pk])
        preview = self.client.post(url, {
            "intent": "preview", "kind": "DEPOSIT",
            "occurred_on": day.isoformat(), "amount": "250.75",
        })
        self.assertContains(preview, "Impact preview")
        response = self.client.post(url, {"intent": "confirm"})
        self.assertRedirects(response, reverse("v2_instance_page", args=[s.pk, "ledger"]))
        entry = s.ledger.get(amount=D("250.75"))
        self.assertEqual(entry.occurred_on, day)
        self.assertEqual(s.ledger.count(), 2)

    def test_ledger_cash_shortcut_keeps_the_actual_date_and_returns_to_ledger(self):
        s = self.finish(date_at=self.today-timedelta(days=5), amount="100")
        day = self.today-timedelta(days=1)
        self.assertContains(self.client.get(reverse("v2_instance_page", args=[s.pk, "ledger"])), "Ledger")
        url = reverse("v2_ledger_add", args=[s.pk])
        self.client.post(url, {
            "intent": "preview", "kind": "DEPOSIT",
            "occurred_on": day.isoformat(), "amount": "120.25",
        })
        self.assertRedirects(self.client.post(url, {"intent": "confirm"}),
            reverse("v2_instance_page", args=[s.pk, "ledger"]))
        entry = s.ledger.get(amount=D("120.25"))
        self.assertEqual(entry.occurred_on, day)

    def test_enrolment_manifest_rejects_unsupported_profile(self):
        with self.assertRaises(InstrumentMappingUnavailable):
            enrolment_manifest(account_type="GIA")
        with self.assertRaises(InstrumentMappingUnavailable):
            enrolment_manifest(broker="UNKNOWN")
        self.assertEqual(enrolment_manifest(), enrolment_manifest())


