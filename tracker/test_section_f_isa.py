"""Section F: UK ISA advisory and zero-invention onboarding/import regressions."""
from datetime import date
from decimal import Decimal as D
from io import BytesIO

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from tracker.core.isa_allowance import estimate_allowance, tax_year_bounds
from tracker.core.catalogue import CATALOGUE_VERSION, onboarding_manifest, released_version
from tracker.cockpit_forms import OneScreenSetupForm
from tracker.instrument_mapping import enrolment_manifest, for_strategy
from tracker.models import Account, LedgerEntry, StrategyInstance, StrategyPreset
from tracker.services import ledger_summary, validate_and_record


class IsaAllowancePolicyTests(SimpleTestCase):
    def test_tax_year_boundary_5_to_6_april(self):
        self.assertEqual(tax_year_bounds(date(2026, 4, 5)),
                         (date(2025, 4, 6), date(2026, 4, 5)))
        self.assertEqual(tax_year_bounds(date(2026, 4, 6)),
                         (date(2026, 4, 6), date(2027, 4, 5)))

    def test_tracked_cash_only_not_automatic_approval(self):
        result = estimate_allowance(
            as_of=date(2026, 10, 19),
            recorded_deposits=(
                (date(2026, 4, 5), D("9999")),
                (date(2026, 4, 6), D("15000")),
                (date(2027, 4, 5), D("500")),
                (date(2027, 4, 6), D("1000")),
            ))
        self.assertEqual(result.tax_year_label, "2026/27")
        self.assertEqual(result.recorded_deposits_gbp, D("15500"))
        self.assertIsNone(result.remaining_if_complete_gbp)
        self.assertEqual(result.status, "partial")
        self.assertIn("Other ISA subscriptions are unknown", result.warning)

    def test_advisory_threshold_combines_explicit_other_isa_amount(self):
        result = estimate_allowance(
            as_of=date(2026, 10, 19),
            recorded_deposits=((date(2026, 6, 7), D("2000")),),
            other_isa_subscriptions=D("19000"))
        self.assertEqual(result.status, "limit_reached")
        self.assertEqual(result.known_total_gbp, D("21000"))
        self.assertEqual(result.remaining_if_complete_gbp, D("0"))
        self.assertIn("advisory", result.warning)

    def test_withdrawal_does_not_erase_subscriptions(self):
        # A withdrawal is intentionally NOT a deposit row. ISA replacement
        # allowances and transfers require broker/HMRC evidence not held here.
        result = estimate_allowance(
            as_of=date(2026, 10, 19),
            recorded_deposits=((date(2026, 10, 10), D("20000")),))
        self.assertEqual(result.status, "limit_reached")

    def test_invalid_or_unverified_year_never_claims_a_limit(self):
        with self.assertRaises(ValueError):
            estimate_allowance(
                as_of=date(2028, 4, 6), recorded_deposits=())
        with self.assertRaises(ValueError):
            estimate_allowance(
                as_of=date(2026, 10, 1), recorded_deposits=(),
                other_isa_subscriptions=D("-1"))

    def test_setup_never_ignores_conflicting_or_unmatched_cash_fields(self):
        choices = (
            {"position_mode": "EXISTING", "start_date": "2026-07-01"},
            {"position_mode": "FRESH", "start_date": "2026-07-01"},
            {"position_mode": "FRESH", "starting_cash": "1000"},
        )
        for overrides in choices:
            data = {"position_mode": "FRESH", "monthly_target": "500",
                    "destination": "NONE", **overrides}
            with self.subTest(overrides=overrides):
                form = OneScreenSetupForm(data)
                self.assertFalse(form.is_valid())


class OwnerContributionAndImportTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(
            username="section-f-owner", password="test-secure-password")
        self.account = Account.objects.create(
            owner=self.owner, name="My ISA", broker="TRADING212", kind="ISA")
        preset, _ = StrategyPreset.objects.get_or_create(
            slug="nasdaq-quarterly-target",
            defaults={"name": "9Sig", "summary": "", "risk": "HIGH", "active": True})
        self.strategy = StrategyInstance.objects.create(
            owner=self.owner, account=self.account, preset=preset,
            name="9Sig", equity_symbol="LQQ3.L", safe_symbol="ERNS.L",
            market_data_enabled=True,
            mapping_version=enrolment_manifest()["mapping_version"],
            resolved_instruments=enrolment_manifest())
        self.client.force_login(self.owner)

    def v2_deposit_url(self):
        # Only an explicitly pinned tracking instance can use the audited v2
        # deposit flow. Existing legacy data is never silently re-versioned.
        StrategyInstance.objects.filter(pk=self.strategy.pk).update(
            strategy_key="nasdaq-quarterly-target",
            platform_code="TRADING212", account_type_code="ISA",
            started_on=date(2026, 1, 1),
            owner_checked_on=timezone.localdate(), tracking_only=True,
            strategy_version=released_version("nasdaq-quarterly-target"),
            mapping_version=CATALOGUE_VERSION,
            resolved_instruments=onboarding_manifest(
                "nasdaq-quarterly-target", "TRADING212", "ISA"),
        )
        return reverse("v2_ledger_add", args=[self.strategy.pk])


    def test_actual_deposit_above_isa_allowance_warns_but_is_recorded(self):
        url = self.v2_deposit_url()
        preview = self.client.post(url, {
            "intent": "preview", "kind": "DEPOSIT",
            "amount": "2000", "occurred_on": "2026-10-19",
            "other_isa_subscriptions": "19000",
        })
        self.assertEqual(preview.status_code, 200)
        self.assertContains(preview, "ISA allowance")
        self.assertEqual(self.strategy.ledger.count(), 0)
        response = self.client.post(url, {"intent": "confirm"})
        self.assertRedirects(response, reverse(
            "v2_instance_page", args=[self.strategy.pk, "ledger"]))
        self.assertEqual(self.strategy.ledger.filter(kind="DEPOSIT").count(), 1)
        messages = [str(message) for message in response.wsgi_request._messages]
        self.assertTrue(any("ISA allowance" in m for m in messages))

    def test_other_isa_information_never_creates_a_deposit(self):
        url = self.v2_deposit_url()
        self.client.post(url, {
            "intent": "preview", "kind": "DEPOSIT",
            "amount": "100.00", "occurred_on": "2026-10-19",
            "other_isa_subscriptions": "15000",
        })
        self.assertEqual(self.strategy.ledger.count(), 0)
        self.client.post(url, {"intent": "confirm"})
        self.assertEqual(ledger_summary(self.strategy)["net_contributed"], D("100"))
        self.assertEqual(self.strategy.ledger.count(), 1)

    def test_new_portfolio_manifest_is_pinned_exactly(self):
        self.assertIsNotNone(for_strategy(self.strategy))
        self.strategy.resolved_instruments["roles"]["growth"]["isin"] = "OTHER"
        self.strategy.save(update_fields=["resolved_instruments"])
        self.assertIsNone(for_strategy(self.strategy))


class OneScreenOwnerEnrolmentTests(TestCase):
    """Original ISA safety expectations, adapted to v2's account-separated wizard."""

    def setUp(self):
        self.owner = get_user_model().objects.create_user(
            username="section-f-enrol-owner", password="test-only-secret")
        StrategyPreset.objects.get_or_create(
            slug="nasdaq-quarterly-target",
            defaults={"name": "9Sig", "risk": "HIGH",
                      "summary": "", "active": True})
        self.client.force_login(self.owner)
        self.url = reverse("v2_add_strategy")

    def enroll(self, *, mode="fresh", name="9Sig ISA", amount="18000.00",
               cash="0"):
        self.client.get(self.url + "?restart=1")
        self.client.post(self.url, {
            "step": "1", "strategy": "nasdaq-quarterly-target",
        })
        self.client.post(self.url, {
            "step": "2", "platform": "TRADING212", "account_type": "ISA",
        })
        self.client.post(self.url, {"step": "3", "ack": "yes"})
        step = {
            "step": "4", "mode": mode, "name": name,
            "start": "2026-10-10",
        }
        if mode == "fresh":
            step.update(amount=amount, deposited="yes")
        else:
            step.update(cash=cash, shares_growth="0", cost_growth="0",
                        shares_safe="0", cost_safe="0")
        self.assertContains(self.client.post(self.url, step), "step 5 of 5")
        response = self.client.post(self.url, {"step": "5"})
        strategy = StrategyInstance.objects.get(owner=self.owner, name=name)
        self.assertRedirects(response, reverse(
            "v2_instance_page", args=[strategy.pk, "today"]))
        return strategy

    def test_reuses_empty_exact_isa_and_only_records_true_deposit(self):
        # v2 deliberately creates an isolated account rather than mutating
        # an unrelated existing ISA with unknown historical funding.
        previous = Account.objects.create(
            owner=self.owner, name="Trading 212 ISA", kind="ISA",
            broker="TRADING212", country="GB")
        strategy = self.enroll()
        self.assertEqual(Account.objects.count(), 2)
        self.assertNotEqual(strategy.account_id, previous.pk)
        self.assertEqual(strategy.mapping_version, enrolment_manifest()["mapping_version"])
        self.assertEqual(strategy.resolved_instruments, enrolment_manifest())
        self.assertEqual(strategy.ledger.filter(kind="DEPOSIT").count(), 1)
        self.assertEqual(strategy.ledger.get().amount, D("18000"))
        self.assertFalse(strategy.ledger.filter(kind="BUY").exists())
        self.assertTrue(strategy.tracking_only)
        self.assertEqual(strategy.monthly_target, D("0"))
        self.assertEqual(Account.objects.get(pk=previous.pk).broker, "TRADING212")

    def test_existing_cash_becomes_the_opening_position(self):
        strategy = self.enroll(mode="hold", cash="175")
        self.assertEqual(strategy.ledger.get().amount, D("175.00"))
        self.assertFalse(strategy.prices.exists())

    def test_mismatched_existing_account_blocks_without_partial_changes(self):
        prior = Account.objects.create(
            owner=self.owner, name="Trading 212 ISA", kind="GIA",
            broker="OTHER", country="GB")
        strategy = self.enroll(mode="hold", cash="100")
        prior.refresh_from_db()
        self.assertEqual(prior.kind, "GIA")
        self.assertEqual(prior.broker, "OTHER")
        self.assertNotEqual(strategy.account_id, prior.pk)
        self.assertEqual(strategy.account.kind, "ISA")
        self.assertEqual(strategy.account.broker, "TRADING212")
        self.assertEqual(strategy.ledger.get().amount, D("100.00"))
