"""Per-user tenancy: the active-user context, the tenant managers, the middleware and the check."""
from datetime import timedelta
from decimal import Decimal as D
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from tenancy.context import NoTenantContext, current_tenant_id, tenant_context
from tracker.core.catalogue import CATALOGUE_VERSION, onboarding_manifest, released_version
from tracker.models import Account, LedgerEntry, StrategyInstance, StrategyPreset
from tracker.services import validate_and_record


def _strategy(owner, name):
    preset, _ = StrategyPreset.objects.get_or_create(
        slug="nasdaq-quarterly-target",
        defaults={"name": "9Sig", "risk": "HIGH", "summary": "Fixed rules", "active": True})
    account = Account.objects.create(owner=owner, name=name + " ISA", kind="ISA", country="GB",
                                     broker="TRADING212")
    return StrategyInstance.objects.create(
        owner=owner, account=account, preset=preset, name=name,
        strategy_key="nasdaq-quarterly-target", platform_code="TRADING212", account_type_code="ISA",
        started_on=timezone.localdate() - timedelta(days=10), owner_checked_on=timezone.localdate(),
        strategy_version=released_version("nasdaq-quarterly-target"), mapping_version=CATALOGUE_VERSION,
        resolved_instruments=onboarding_manifest("nasdaq-quarterly-target", "TRADING212", "ISA"),
        equity_symbol="LQQ3.L", safe_symbol="ERNS.L", tracking_only=True)


class TenancyBase(TestCase):
    def setUp(self):
        User = get_user_model()
        self.alice = User.objects.create_user("alice", password="x" * 12)
        self.bob = User.objects.create_user("bob", password="x" * 12)
        self.a_strategy = _strategy(self.alice, "Alice strategy")
        self.b_strategy = _strategy(self.bob, "Bob strategy")
        day = timezone.localdate() - timedelta(days=3)
        validate_and_record(self.a_strategy, LedgerEntry(kind="DEPOSIT", amount=D("100"), occurred_on=day))
        validate_and_record(self.b_strategy, LedgerEntry(kind="DEPOSIT", amount=D("200"), occurred_on=day))


class ContextAndManagerTests(TenancyBase):
    def test_no_context_fails_closed(self):
        self.assertIsNone(current_tenant_id())
        for model in (Account, StrategyInstance, LedgerEntry):
            with self.assertRaises(NoTenantContext):
                list(model.tenant_objects.all())

    def test_each_user_sees_only_their_own_rows_direct_and_through_the_strategy(self):
        with tenant_context(self.alice):
            self.assertEqual({a.owner_id for a in Account.tenant_objects.all()}, {self.alice.pk})
            self.assertEqual(list(StrategyInstance.tenant_objects.values_list("pk", flat=True)), [self.a_strategy.pk])
            self.assertEqual({e.amount for e in LedgerEntry.tenant_objects.all()}, {D("100.00")})
            self.assertFalse(StrategyInstance.tenant_objects.filter(pk=self.b_strategy.pk).exists())
        with tenant_context(self.bob):
            self.assertEqual({e.amount for e in LedgerEntry.tenant_objects.all()}, {D("200.00")})
        self.assertIsNone(current_tenant_id())

    def test_context_nests_and_always_resets(self):
        with tenant_context(self.alice):
            with tenant_context(self.bob):
                self.assertEqual(current_tenant_id(), self.bob.pk)
            self.assertEqual(current_tenant_id(), self.alice.pk)
        with self.assertRaises(RuntimeError):
            with tenant_context(self.alice):
                raise RuntimeError("boom")
        self.assertIsNone(current_tenant_id())

    def test_plain_manager_is_unscoped_for_platform_code(self):
        self.assertEqual(StrategyInstance.objects.count(), 2)


class MiddlewareTests(TenancyBase):
    def test_request_is_scoped_to_the_signed_in_user_and_cleared_after(self):
        self.client.force_login(self.alice)
        response = self.client.get(reverse("v2_overview"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.wsgi_request.tenant_id, self.alice.pk)
        self.assertIsNone(current_tenant_id())

    def test_anonymous_request_has_no_tenant(self):
        response = self.client.get(reverse("login"))
        self.assertIsNone(response.wsgi_request.tenant_id)

    def test_a_new_user_without_any_data_can_open_the_main_pages(self):
        carol = get_user_model().objects.create_user("carol", password="x" * 12)
        self.client.force_login(carol)
        for name in ("v2_overview", "v2_settings", "v2_add_strategy"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)

    def test_an_inactive_user_cannot_sign_in(self):
        self.alice.is_active = False
        self.alice.save()
        self.assertFalse(self.client.login(username="alice", password="x" * 12))


class OwnershipCheckTests(TenancyBase):
    def test_check_passes_when_clean_and_fails_when_a_strategy_and_account_disagree(self):
        out = StringIO()
        call_command("tenancy_check", stdout=out)
        self.assertIn("Ownership is consistent", out.getvalue())
        self.assertIn("all ", out.getvalue())
        StrategyInstance.objects.filter(pk=self.a_strategy.pk).update(owner=self.bob)
        with self.assertRaises(CommandError):
            call_command("tenancy_check", stdout=StringIO())

    def test_ledger_digests_are_stable_and_per_strategy(self):
        first, second = StringIO(), StringIO()
        call_command("tenancy_check", "--digest", stdout=first)
        call_command("tenancy_check", "--digest", stdout=second)
        self.assertEqual(first.getvalue(), second.getvalue())
        self.assertIn("strategy %d " % self.a_strategy.pk, first.getvalue())
