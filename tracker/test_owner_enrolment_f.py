"""Section F: real-money-only ISA enrolment, contribution and T212 import UX."""
from datetime import timedelta
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from tracker.cockpit_forms import OneScreenSetupForm
from tracker.instrument_mapping import (
    MAPPING_VERSION, enrolment_manifest, InstrumentMappingUnavailable,
)
from tracker.models import (
    Account, BrokerImportRow, LedgerEntry, StrategyInstance, StrategyPreset,
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

    def data(self, **changes):
        original = {
            "position_mode": "FRESH", "starting_cash": "",
            "start_date": "", "monthly_target": "500",
            "destination": "NONE", "email": "",
        }
        original.update(changes)
        return original

    def test_one_screen_fixed_isa_scope_and_named_issuer_evidence(self):
        page = self.client.get(reverse("cockpit_setup"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Stocks &amp; Shares ISA")
        self.assertContains(page, "IE00BLRPRL42")
        self.assertContains(page, "IE00BCRY6441")
        self.assertContains(page, "Invest")
        self.assertNotContains(page, 'value="GIA"')
        self.assertNotContains(page, 'name="account_kind"')
        self.assertContains(page, 'name="monthly_target"')

    def test_plan_is_never_recorded_as_real_funding(self):
        response = self.client.post(reverse("cockpit_setup"), self.data())
        self.assertRedirects(response, reverse("cockpit_today"))
        s = StrategyInstance.objects.get(owner=self.owner)
        self.assertEqual(s.monthly_target, D("500"))
        self.assertEqual(s.account.kind, "ISA")
        self.assertEqual(s.account.country, "GB")
        self.assertEqual(s.account.broker, "TRADING212")
        self.assertEqual(s.account.currency, "GBP")
        self.assertEqual(s.ledger.count(), 0)
        self.assertEqual(s.mapping_version, MAPPING_VERSION)
        self.assertEqual(s.equity_symbol, "LQQ3.L")
        self.assertEqual(s.safe_symbol, "ERNS.L")
        self.assertTrue(s.strategy_version.startswith("nine_sig."))
        self.assertEqual(s.resolved_instruments["roles"]["growth"]["isin"], "IE00BLRPRL42")
        self.assertEqual(s.resolved_instruments["roles"]["safe"]["isin"], "IE00BCRY6441")
        self.assertEqual(s.resolved_instruments["roles"]["growth"]["raw_quote_unit"], "GBX")
        self.assertEqual(s.resolved_instruments["roles"]["safe"]["raw_quote_unit"], "GBP")
        self.assertFalse(s.resolved_instruments["roles"]["growth"]["broker_isa_confirmed"])
        self.assertFalse(s.resolved_instruments["roles"]["growth"]["owner_product_permission_confirmed"])
        self.assertEqual(len(s.resolved_instruments["sha256"]), 64)

    def test_fresh_owner_provided_cash_records_exact_date_once(self):
        actual_date = self.today - timedelta(days=4)
        response = self.client.post(reverse("cockpit_setup"),
            self.data(starting_cash="1000.00", start_date=actual_date.isoformat()))
        self.assertRedirects(response, reverse("cockpit_today"))
        s = StrategyInstance.objects.get(owner=self.owner)
        entry = s.ledger.get()
        self.assertEqual(entry.kind, "DEPOSIT")
        self.assertEqual(entry.amount, D("1000"))
        self.assertEqual(entry.occurred_on, actual_date)
        self.assertEqual(s.ledger.count(), 1)
        self.client.post(reverse("cockpit_setup"),
            self.data(starting_cash="1000", start_date=actual_date.isoformat()))
        self.assertEqual(StrategyInstance.objects.filter(owner=self.owner).count(), 1)
        self.assertEqual(s.ledger.count(), 1)

    def test_existing_holdings_begin_empty_and_go_to_csv_preview(self):
        response = self.client.post(reverse("cockpit_setup"),
            self.data(position_mode="EXISTING"))
        self.assertRedirects(response, reverse("cockpit_csv"))
        strategy = StrategyInstance.objects.get(owner=self.owner)
        self.assertEqual(strategy.ledger.count(), 0)
        self.assertContains(self.client.get(reverse("cockpit_csv")),
                            "Preview without importing")

    def test_existing_holdings_reject_double_counted_opening_cash(self):
        response = self.client.post(reverse("cockpit_setup"),
            self.data(position_mode="EXISTING", starting_cash="1000",
                      start_date=self.today.isoformat()))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "do not add a second opening deposit")
        self.assertFalse(StrategyInstance.objects.filter(owner=self.owner).exists())
        self.assertFalse(Account.objects.filter(owner=self.owner).exists())

    def test_fresh_requires_actual_date_for_recorded_cash(self):
        response = self.client.post(reverse("cockpit_setup"),
            self.data(starting_cash="1000"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Enter the date this money actually arrived")
        self.assertFalse(StrategyInstance.objects.filter(owner=self.owner).exists())

    def test_cannot_backfill_future_cash_and_gia_request_is_not_offered(self):
        future = self.today + timedelta(days=1)
        response = self.client.post(reverse("cockpit_setup"),
            self.data(starting_cash="50", start_date=future.isoformat()))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Account.objects.filter(owner=self.owner).exists())
        data = self.data(account_kind="GIA")  # no corresponding form field
        response = self.client.post(reverse("cockpit_setup"), data)
        self.assertRedirects(response, reverse("cockpit_today"))
        self.assertEqual(StrategyInstance.objects.get(owner=self.owner).account.kind, "ISA")

    def test_optional_notifications_and_monthly_zero_remain_planning(self):
        response = self.client.post(reverse("cockpit_setup"),
            self.data(monthly_target="0", destination="EMAIL", email=""))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Enter an alert email")
        self.assertFalse(StrategyInstance.objects.filter(owner=self.owner).exists())
        response = self.client.post(reverse("cockpit_setup"),
            self.data(monthly_target="0", destination="EMAIL", email="owner@example.com"))
        self.assertRedirects(response, reverse("cockpit_today"))
        s = StrategyInstance.objects.get(owner=self.owner)
        self.assertEqual(s.monthly_target, D("0"))
        self.assertEqual(s.ledger.count(), 0)
        pref = self.owner.notification_preference
        self.assertTrue(pref.email_enabled)
        self.assertFalse(pref.discord_enabled)
        self.assertEqual(pref.destination_email, "owner@example.com")

    def test_owner_can_record_actual_contribution_after_enrolling(self):
        self.client.post(reverse("cockpit_setup"), self.data())
        actual_date = self.today - timedelta(days=2)
        response = self.client.post(reverse("cockpit_deposit"),
            {"amount": "250.75", "occurred_on": actual_date.isoformat()})
        self.assertRedirects(response, reverse("cockpit_today"))
        s = StrategyInstance.objects.get(owner=self.owner)
        entry = s.ledger.get()
        self.assertEqual(entry.amount, D("250.75"))
        self.assertEqual(entry.occurred_on, actual_date)

    def test_ledger_cash_shortcut_keeps_the_actual_date_and_returns_to_ledger(self):
        self.client.post(reverse("cockpit_setup"), self.data())
        actual_date = self.today - timedelta(days=1)
        response = self.client.get(reverse("cockpit_ledger"))
        self.assertContains(response, "Date money arrived")
        response = self.client.post(reverse("cockpit_deposit"), {
            "amount": "120.25", "occurred_on": actual_date.isoformat(),
            "return_to": "ledger"})
        self.assertRedirects(response, reverse("cockpit_ledger"))
        entry = StrategyInstance.objects.get(owner=self.owner).ledger.get()
        self.assertEqual(entry.amount, D("120.25"))
        self.assertEqual(entry.occurred_on, actual_date)

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
        account = Account.objects.create(owner=self.owner, name="ISA", kind="ISA",
                                         broker="TRADING212")
        preset, _ = StrategyPreset.objects.get_or_create(
            slug="nasdaq-quarterly-target",
            defaults={"name": "9Sig", "summary": "", "risk": "HIGH"})
        self.strategy = StrategyInstance.objects.create(
            owner=self.owner, account=account, preset=preset, name="9Sig",
            equity_symbol="LQQ3.L", safe_symbol="ERNS.L")
        self.client.force_login(self.owner)

    def csv_file(self, text):
        return SimpleUploadedFile("sample.csv", text.encode("utf-8"),
                                  content_type="text/csv")

    def test_preview_is_read_only_and_exact_file_confirmation_is_explicit(self):
        csv_text = ("Action,Time,Total (GBP),Ticker,No. of shares,Price / share,"
                    "Currency (Price / share),ISIN,ID\n"
                    "Deposit,2026-01-02 12:00:00,1000,,,,,,dep-1\n"
                    "Market buy,2026-01-03 12:00:00,500,LQQ3,5,100,GBP,IE00BLRPRL42,buy-1\n")
        response = self.client.post(reverse("cockpit_csv"),
                                    {"csv_file": self.csv_file(csv_text)})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Preview complete")
        self.assertContains(response, "Confirm exactly this file")
        self.assertContains(response, "sample.csv")
        self.assertEqual(self.strategy.ledger.count(), 0)
        self.assertEqual(BrokerImportRow.objects.count(), 0)
        changed = csv_text.replace("1000,,,,,,dep-1", "1010,,,,,,dep-1")
        response = self.client.post(reverse("cockpit_csv"), {
            "csv_file": self.csv_file(changed), "apply": "yes"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.strategy.ledger.count(), 0)
        self.assertEqual(BrokerImportRow.objects.count(), 0)
        self.client.post(reverse("cockpit_csv"),
            {"csv_file": self.csv_file(csv_text)})
        confirmed = self.client.post(reverse("cockpit_csv"), {
            "csv_file": self.csv_file(csv_text), "apply": "yes"})
        self.assertRedirects(confirmed, reverse("cockpit_ledger"))
        self.assertEqual(self.strategy.ledger.count(), 2)
        self.assertEqual(BrokerImportRow.objects.count(), 2)

    def test_errors_show_all_or_nothing_preview_reason(self):
        bad = ("Action,Time,Total (GBP),ID\n"
               "Deposit,2026-01-02 12:00:00,1000,d1\n"
               "Currency conversion,2026-01-02 12:01:00,100,fx-1\n")
        response = self.client.post(reverse("cockpit_csv"),
                                    {"csv_file": self.csv_file(bad)})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nothing was imported")
        self.assertContains(response, "CSV row 3")
        self.assertNotContains(response, "Confirm exactly this file")
        self.assertFalse(self.strategy.ledger.exists())
