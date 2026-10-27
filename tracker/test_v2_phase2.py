"""Phase 2 verified identity, catalogue, onboarding and owner isolation tests."""
from datetime import timedelta
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from tracker.core.catalogue import (
    ALL_WEATHER, CATALOGUE, CATALOGUE_VERSION, OFFERS, STRATEGY_9SIG,
    MappingNotAvailable, onboarding_manifest, rows_for, selection_available,
)
from tracker.v2_onboarding import OPENING_NOTE
from tracker.models import (
    Account, InstanceOpeningSnapshot, LedgerEntry, StrategyInstance,
)


class Phase2CatalogueTests(TestCase):
    def test_exact_reviewed_isa_and_gia_pairs_no_false_permission(self):
        for account_type in ("ISA", "GIA"):
            with self.subTest(account_type=account_type):
                rows = rows_for(STRATEGY_9SIG, "TRADING212", account_type)
                self.assertEqual({row.role: row.ticker for row in rows}, {
                    "growth": "LQQ3.L", "safe": "ERNS.L",
                })
                self.assertEqual({row.isin for row in rows}, {
                    "IE00BLRPRL42", "IE00BCRY6441",
                })
                self.assertEqual({row.role: row.raw_quote_unit for row in rows}, {
                    "growth": "GBX", "safe": "GBP",
                })
                self.assertTrue(all(row.status == "account_check_pending" for row in rows))
                self.assertTrue(all(row.verified_on and row.issuer_url for row in rows))
                self.assertTrue(selection_available(STRATEGY_9SIG, "TRADING212", account_type))
                manifest = onboarding_manifest(STRATEGY_9SIG, "TRADING212", account_type)
                self.assertEqual(manifest["account_type"], account_type)
                self.assertFalse(manifest["roles"]["growth"]["broker_isa_confirmed"])
                self.assertFalse(manifest["roles"]["growth"]["yahoo_price_unit_confirmed"])
                self.assertEqual(len(manifest["sha256"]), 64)

    def test_all_weather_pins_five_exact_lines_with_the_account_hold(self):
        self.assertTrue(OFFERS[ALL_WEATHER].enabled)
        expected = {"stocks": ("VUAG.L", "IE00BFMXXD54", "GBP"), "long": ("GLTL.L", "IE00B6YX5L24", "GBP"),
                    "inter": ("IGLT.L", "IE00B1FZSB30", "GBP"), "gold": ("SGLN.L", "IE00B4ND3602", "GBX"),
                    "comm": ("CMOP.L", "IE00BD6FTQ80", "GBX")}
        for account_type in ("ISA", "GIA"):
            rows = rows_for(ALL_WEATHER, "TRADING212", account_type)
            self.assertEqual({r.role: (r.ticker, r.isin, r.raw_quote_unit) for r in rows}, expected)
            self.assertEqual(sum((row.weight for row in rows), D("0")), D("1"))
            # Source-cited but not first-hand: same hold as 9Sig, never "verified".
            self.assertTrue(all(row.status == "account_check_pending" for row in rows))
            self.assertTrue(all(row.issuer_url and row.broker_evidence_url for row in rows))
            self.assertTrue(selection_available(ALL_WEATHER, "TRADING212", account_type))
            manifest = onboarding_manifest(ALL_WEATHER, "TRADING212", account_type)
            self.assertEqual(manifest["roles"]["gold"]["raw_to_gbp_factor"], "100")
            self.assertFalse(manifest["roles"]["gold"]["broker_isa_confirmed"])
        self.assertFalse(selection_available(ALL_WEATHER, "TRADING212", "SIPP"))

    def test_no_sipp_other_platform_or_version_guess(self):
        self.assertFalse(rows_for(STRATEGY_9SIG, "TRADING212", "SIPP"))
        self.assertFalse(selection_available(STRATEGY_9SIG, "TRADING212", "SIPP"))
        self.assertFalse(selection_available(STRATEGY_9SIG, "AJBELL", "ISA"))
        self.assertTrue(all(row.mapping_version == CATALOGUE_VERSION for row in CATALOGUE.values()))
        with self.assertRaises(MappingNotAvailable):
            rows_for(STRATEGY_9SIG, "TRADING212", "ISA", version="old")


class Phase2WizardTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(
            "v2-phase2-owner", password="good-password-example-123"
        )
        self.other = User.objects.create_user(
            "v2-phase2-other", password="good-password-example-123"
        )
        self.client.force_login(self.owner)
        self.url = reverse("v2_add_strategy")
        self.day = timezone.localdate() - timedelta(days=2)

    def create(self, account_type="ISA", **values):
        """The one-screen setup form, posted once."""
        return self.client.post(self.url, {
            "strategy": STRATEGY_9SIG, "platform": "TRADING212",
            "account_type": account_type, "ack": "yes", "ack_choice": "yes", **values})

    def test_anonymous_cannot_see_or_write_wizard_or_catalogue(self):
        self.client.logout()
        for url in (self.url,):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn("/login/", response["Location"])
        response = self.create(mode="fresh", name="x", start=self.day.isoformat(),
                               amount="10", deposited="yes")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(StrategyInstance.objects.count(), 0)

    def test_wizard_shows_exact_mapped_identifiers(self):
        # The catalogue page is gone; setup shows the exact lines it will pin.
        response = self.client.get(self.url)
        self.assertContains(response, "IE00BLRPRL42")
        self.assertContains(response, "IE00BCRY6441")
        self.assertNotContains(response, "Global equity role")

    def test_unsupported_strategy_account_and_skipped_steps_refuse(self):
        fields = dict(mode="fresh", name="x", start=self.day.isoformat(),
                      amount="10", deposited="yes")
        error = self.client.post(self.url, {"strategy": "no-such-strategy", "platform": "TRADING212",
                                            "account_type": "ISA", "ack": "yes", "ack_choice": "yes", **fields})
        self.assertContains(error, "not available yet")
        refused = self.create(account_type="SIPP", **fields)
        self.assertContains(refused, "No reviewed instruments")
        self.assertEqual(StrategyInstance.objects.count(), 0)

    def test_verified_cash_deposit_creates_exactly_one_owner_ledger_record(self):
        done = self.create(
            mode="fresh", name="My second 9Sig", start=self.day.isoformat(),
            amount="1000.00", deposited="yes",
        )
        instance = StrategyInstance.objects.get(owner=self.owner)
        self.assertRedirects(
            done, reverse("v2_instance_page", args=[instance.pk, "today"]),
        )
        self.assertEqual(instance.strategy_key, STRATEGY_9SIG)
        self.assertEqual(instance.mapping_version, CATALOGUE_VERSION)
        self.assertEqual(instance.resolved_instruments["sha256"],
                         onboarding_manifest(STRATEGY_9SIG, "TRADING212", "ISA")["sha256"])
        self.assertEqual(instance.platform_code, instance.account.broker)
        self.assertEqual(instance.account_type_code, instance.account.kind)
        self.assertEqual(instance.started_on, self.day)
        self.assertEqual(instance.account.owner_id, self.owner.pk)
        self.assertTrue(instance.tracking_only)
        self.assertFalse(instance.market_data_enabled)
        from tracker.instrument_mapping import for_strategy
        self.assertIsNone(for_strategy(instance))
        self.assertTrue(instance.owner_checked_on)
        # Default routes; changed per strategy later on Strategy & alerts.
        self.assertEqual(instance.alert_routes["quarterly"], {"email": True, "discord": True, "telegram": True})
        self.assertEqual(instance.alert_routes["contribution"], {"email": False, "discord": False, "telegram": False})
        self.assertEqual(instance.ledger.count(), 1)
        entry = instance.ledger.get()
        self.assertEqual(entry.kind, LedgerEntry.DEPOSIT)
        self.assertEqual(entry.occurred_on, self.day)
        self.assertEqual(entry.amount, D("1000.00"))
        self.assertEqual(entry.quantity, D("0"))
        self.assertFalse(InstanceOpeningSnapshot.objects.exists())
        response = self.client.get(reverse("v2_instance_page", args=[instance.pk, "today"]))
        self.assertContains(response, "Not live yet")
        self.assertNotContains(response, "SELL LQQ3")
        self.assertNotContains(response, "£14,820")

    def test_existing_shares_become_a_labelled_opening_position(self):
        self.create(
            account_type="GIA", mode="hold", name="Existing 9Sig", start=self.day.isoformat(),
            cash="175.20", shares_growth="2.125", cost_growth="55.50",
            shares_safe="8", cost_safe="101.20",
        )
        instance = StrategyInstance.objects.get(owner=self.owner)
        self.assertEqual(instance.account.kind, "GIA")
        self.assertEqual(instance.onboarding_mode, "hold")
        # Funding = cash + holdings at the start-date prices; one buy per holding.
        self.assertEqual(sorted(instance.ledger.values_list("kind", "symbol", "quantity", "unit_price", "amount")), [
            ("BUY", "ERNS.L", D("8"), D("101.20"), D("0")),
            ("BUY", "LQQ3.L", D("2.125"), D("55.50"), D("0")),
            ("DEPOSIT", "", D("0"), D("0"), D("1102.74")),
        ])
        self.assertTrue(all(e.note == OPENING_NOTE for e in instance.ledger.all()))
        self.assertEqual(instance.prices.count(), 0)
        self.assertFalse(InstanceOpeningSnapshot.objects.filter(strategy=instance).exists())
        self.assertTrue(instance.tracking_only)
        page = self.client.get(reverse("v2_instance_page", args=[instance.pk, "today"]))
        self.assertContains(page, "Based on holdings you entered")
        self.assertNotContains(page, "SECRET_OTHER")

    def test_requires_ack_and_actual_deposit_confirmation(self):
        no_ack = self.create(ack="", mode="fresh", name="No ack", start=self.day.isoformat(),
                             amount="500", deposited="yes")
        self.assertContains(no_ack, "confirm you checked")
        no_choice = self.create(ack_choice="", mode="fresh", name="No choice",
                                start=self.day.isoformat(), amount="500", deposited="yes")
        self.assertContains(no_choice, "chose this strategy yourself")
        self.assertEqual(Account.objects.filter(owner=self.owner).count(), 0)
        error = self.create(mode="fresh", name="No fake deposits",
                            start=self.day.isoformat(), amount="500")
        self.assertContains(error, "actually deposited")
        self.assertEqual(Account.objects.filter(owner=self.owner).count(), 0)
        self.assertEqual(LedgerEntry.objects.count(), 0)

    def test_reject_negative_future_and_invalid_opening_without_partial_write(self):
        cases = [
            {"mode": "fresh", "name": "Invalid", "start": self.day.isoformat(), "amount": "-1", "deposited": "yes"},
            {"mode": "fresh", "name": "Invalid", "start": self.day.isoformat(), "amount": "1.001", "deposited": "yes"},
            {"mode": "fresh", "name": "Invalid", "start": (timezone.localdate()+timedelta(days=1)).isoformat(), "amount": "100", "deposited": "yes"},
            {"mode": "hold", "name": "Invalid", "start": self.day.isoformat(), "cash": "0", "shares_growth": "1", "cost_growth": "0"},
            {"mode": "hold", "name": "Invalid", "start": self.day.isoformat(), "cash": "0", "shares_growth": "0", "cost_growth": "0", "shares_safe": "0", "cost_safe": "0"},
        ]
        for entry in cases:
            with self.subTest(entry=entry):
                response = self.create(**entry)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'role="alert"')
                self.assertEqual(StrategyInstance.objects.count(), 0)
                self.assertEqual(Account.objects.count(), 0)

    def test_identity_immutable_and_repeat_strategy_allowed(self):
        self.create(mode="fresh", name="Same 9Sig", start=self.day.isoformat(),
                    amount="200", deposited="yes")
        first = StrategyInstance.objects.get(owner=self.owner)
        first.account.kind = "GIA"
        with self.assertRaises(ValidationError):
            first.account.save()
        first.refresh_from_db()
        first.strategy_key = ALL_WEATHER
        with self.assertRaises(ValidationError):
            first.save()
        first.refresh_from_db()
        first.account_type_code = "GIA"
        with self.assertRaises(ValidationError):
            first.save()
        first.refresh_from_db()
        self.create(mode="fresh", name="Same 9Sig", start=self.day.isoformat(),
                    amount="100", deposited="yes")
        strategies = list(StrategyInstance.objects.filter(owner=self.owner))
        self.assertEqual(len(strategies), 2)
        self.assertNotEqual(strategies[0].account_id, strategies[1].account_id)
        self.assertNotEqual(strategies[0].account.name, strategies[1].account.name)

    def test_failed_setup_keeps_nothing_for_another_owner(self):
        self.create(mode="fresh", name="Private name", start="", amount="10", deposited="yes")
        self.client.force_login(self.other)
        response = self.client.get(self.url)
        self.assertNotContains(response, "Private name")
        self.assertEqual(StrategyInstance.objects.count(), 0)

    def test_snapshot_cannot_be_mutated_after_recording(self):
        # Older instances may still carry an opening snapshot; it stays immutable.
        self.create(account_type="GIA", mode="fresh", name="Hold", start=self.day.isoformat(),
                    amount="250", deposited="yes")
        opening = InstanceOpeningSnapshot.objects.create(
            strategy=StrategyInstance.objects.get(owner=self.owner),
            reported_on=self.day, cash_gbp=D("250"), positions={})
        opening.cash_gbp = D("1000")
        with self.assertRaises(ValidationError):
            opening.save()
