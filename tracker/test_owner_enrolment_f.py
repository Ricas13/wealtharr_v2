"""Owner-only ISA enrolment, real contributions, and Trading 212 CSV gates in v2.

The retired one-page cockpit setup is gone. These tests exercise the released
five-step wizard and audited v2 Ledger, preserving the no-invented-cash rules.
"""
from datetime import date, timedelta
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from tracker.core.catalogue import (
    CATALOGUE_VERSION, onboarding_manifest, released_version,
)
from tracker.instrument_mapping import enrolment_manifest, InstrumentMappingUnavailable
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

    def to_start(self):
        self.client.get(self.url + "?restart=1")
        self.assertEqual(self.client.post(self.url, {
            "step": "1", "strategy": "nasdaq-quarterly-target",
        }).status_code, 200)
        self.assertEqual(self.client.post(self.url, {
            "step": "2", "platform": "TRADING212", "account_type": "ISA",
        }).status_code, 200)
        self.assertEqual(self.client.post(self.url, {
            "step": "3", "ack": "yes",
        }).status_code, 200)

    def finish(self, *, mode="fresh", date_at=None, amount="1000",
               name="Owner 9Sig ISA", **holdings):
        self.to_start()
        data = {
            "step": "4", "mode": mode, "name": name,
            "start": (date_at or self.today-timedelta(days=4)).isoformat(),
        }
        if mode == "fresh":
            data.update(amount=amount, deposited="yes")
        else:
            data.update(cash="0", shares_growth="0", cost_growth="0",
                        shares_safe="0", cost_safe="0")
            data.update(holdings)
        preview = self.client.post(self.url, data)
        self.assertContains(preview, "step 5 of 5")
        done = self.client.post(self.url, {"step": "5", "alert_quarterly_email": "on"})
        instance = StrategyInstance.objects.get(owner=self.owner, name=name)
        self.assertRedirects(
            done, reverse("v2_instance_page", args=[instance.pk, "today"]))
        return instance

    def test_one_screen_fixed_isa_scope_and_named_issuer_evidence(self):
        self.to_start()
        page = self.client.get(self.url)
        self.assertContains(page, "IE00BLRPRL42")
        self.assertContains(page, "IE00BCRY6441")
        self.assertContains(page, "Trading 212")
        self.assertNotContains(page, 'name="equity_symbol"')
        self.assertNotContains(page, 'name="safe_symbol"')

    def test_plan_is_never_recorded_as_real_funding(self):
        # A reported opening holding is NOT a backdated deposit or invented BUY.
        s = self.finish(mode="hold", cash="250")
        self.assertEqual(s.account.kind, "ISA")
        self.assertEqual(s.account.country, "GB")
        self.assertEqual(s.account.broker, "TRADING212")
        self.assertEqual(s.account.currency, "GBP")
        self.assertEqual(s.ledger.count(), 0)
        self.assertEqual(InstanceOpeningSnapshot.objects.get(strategy=s).cash_gbp, D("250"))
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

    def test_existing_holdings_begin_empty_and_go_to_csv_preview(self):
        s = self.finish(mode="hold", cash="50", shares_growth="2", cost_growth="100")
        self.assertEqual(s.ledger.count(), 0)
        self.assertEqual(InstanceOpeningSnapshot.objects.get(strategy=s).positions["growth"]["shares"], "2")
        url = reverse("v2_ledger_import", args=[s.pk])
        self.assertContains(self.client.get(url), "Preview CSV without saving")

    def test_existing_holdings_reject_double_counted_opening_cash(self):
        self.to_start()
        response = self.client.post(self.url, {
            "step": "4", "mode": "hold", "name": "No fictitious cost",
            "start": self.today.isoformat(), "cash": "500",
            "shares_growth": "0", "cost_growth": "100",
        })
        self.assertContains(response, "average cost")
        self.assertFalse(StrategyInstance.objects.filter(owner=self.owner).exists())
        self.assertFalse(Account.objects.filter(owner=self.owner).exists())

    def test_fresh_requires_actual_date_for_recorded_cash(self):
        self.to_start()
        response = self.client.post(self.url, {
            "step": "4", "mode": "fresh", "name": "Missing date",
            "start": "", "amount": "1000", "deposited": "yes",
        })
        self.assertContains(response, "valid start date")
        self.assertFalse(StrategyInstance.objects.filter(owner=self.owner).exists())

    def test_cannot_backfill_future_cash_and_gia_request_is_not_offered(self):
        self.to_start()
        future = self.today + timedelta(days=1)
        invalid = self.client.post(self.url, {
            "step": "4", "mode": "fresh", "name": "Future not real",
            "start": future.isoformat(), "amount": "50", "deposited": "yes",
        })
        self.assertContains(invalid, "future")
        self.assertFalse(Account.objects.filter(owner=self.owner).exists())
        # Forged old account_kind is ignored after explicit ISA wizard selection.
        valid = self.client.post(self.url, {
            "step": "4", "mode": "fresh", "name": "Real account",
            "start": self.today.isoformat(), "amount": "50",
            "deposited": "yes", "account_kind": "GIA",
        })
        self.assertContains(valid, "step 5 of 5")
        self.client.post(self.url, {"step": "5"})
        self.assertEqual(StrategyInstance.objects.get(owner=self.owner).account.kind, "ISA")

    def test_optional_notifications_and_monthly_zero_remain_planning(self):
        s = self.finish(mode="hold", cash="50")
        self.assertEqual(s.monthly_target, D("0"))
        self.assertEqual(s.ledger.count(), 0)
        self.assertTrue(s.alert_routes["quarterly"]["email"])
        self.assertFalse(s.alert_routes["quarterly"]["discord"])

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


class Trading212PreviewJourneyTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(
            username="section_f_importer", password="strong-example-test-password")
        account = Account.objects.create(
            owner=self.owner, name="ISA", kind="ISA", broker="TRADING212")
        preset, _ = StrategyPreset.objects.get_or_create(
            slug="nasdaq-quarterly-target",
            defaults={"name": "9Sig", "summary": "", "risk": "HIGH"})
        self.strategy = StrategyInstance.objects.create(
            owner=self.owner, account=account, preset=preset, name="9Sig",
            equity_symbol="LQQ3.L", safe_symbol="ERNS.L",
            strategy_key="nasdaq-quarterly-target", platform_code="TRADING212",
            account_type_code="ISA", started_on=date(2026, 1, 1),
            owner_checked_on=timezone.localdate(), tracking_only=True,
            strategy_version=released_version("nasdaq-quarterly-target"),
            mapping_version=CATALOGUE_VERSION,
            resolved_instruments=onboarding_manifest(
                "nasdaq-quarterly-target", "TRADING212", "ISA"))
        self.url = reverse("v2_ledger_import", args=[self.strategy.pk])
        self.client.force_login(self.owner)

    def csv_file(self, text):
        return SimpleUploadedFile(
            "sample.csv", text.encode("utf-8"), content_type="text/csv")

    def test_preview_is_read_only_and_exact_file_confirmation_is_explicit(self):
        csv_text = (
            "Action,Time,Total (GBP),Ticker,No. of shares,Price / share,"
            "Currency (Price / share),ISIN,ID\n"
            "Deposit,2026-01-02 12:00:00,1000,,,,,,dep-1\n"
            "Market buy,2026-01-03 12:00:00,500,LQQ3,5,100,GBP,IE00BLRPRL42,buy-1\n")
        response = self.client.post(self.url, {"csv_file": self.csv_file(csv_text)})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Preview passed")
        self.assertContains(response, "exact same file")
        self.assertContains(response, "sample.csv")
        self.assertEqual(self.strategy.ledger.count(), 0)
        self.assertEqual(BrokerImportRow.objects.count(), 0)
        changed = csv_text.replace("1000,,,,,,dep-1", "1010,,,,,,dep-1")
        refused = self.client.post(self.url, {
            "csv_file": self.csv_file(changed), "apply": "yes"})
        self.assertEqual(refused.status_code, 200)
        self.assertEqual(self.strategy.ledger.count(), 0)
        self.client.post(self.url, {"csv_file": self.csv_file(csv_text)})
        confirmed = self.client.post(self.url, {
            "csv_file": self.csv_file(csv_text), "apply": "yes"})
        self.assertRedirects(confirmed, reverse(
            "v2_instance_page", args=[self.strategy.pk, "ledger"]))
        self.assertEqual(self.strategy.ledger.count(), 2)
        self.assertEqual(BrokerImportRow.objects.count(), 2)

    def test_errors_show_all_or_nothing_preview_reason(self):
        bad = (
            "Action,Time,Total (GBP),ID\n"
            "Deposit,2026-01-02 12:00:00,1000,d1\n"
            "Currency conversion,2026-01-02 12:01:00,100,fx-1\n")
        response = self.client.post(self.url, {"csv_file": self.csv_file(bad)})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nothing can be imported")
        self.assertContains(response, "Line 3")
        self.assertNotContains(response, "Confirm original CSV import")
        self.assertFalse(self.strategy.ledger.exists())
