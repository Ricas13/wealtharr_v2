"""Tenancy core: context, tenant-scoped managers, stamping, middleware, backfill and the check."""
import importlib
from datetime import timedelta
from decimal import Decimal as D
from io import StringIO

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from tenancy.context import NoTenantContext, current_organization_id, tenant_context
from tenancy.middleware import SESSION_KEY, switch_organization
from tenancy.models import Membership, Organization
from tenancy.services import default_membership, membership_for, personal_org_for
from tracker.models import Account, LedgerEntry, StrategyInstance
from tracker.services import validate_and_record
from tracker.core.catalogue import CATALOGUE_VERSION, onboarding_manifest, released_version
from tracker.models import StrategyPreset


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
        self.a_org = personal_org_for(self.alice)
        self.b_org = personal_org_for(self.bob)
        day = timezone.localdate() - timedelta(days=3)
        validate_and_record(self.a_strategy, LedgerEntry(kind="DEPOSIT", amount=D("100"), occurred_on=day))
        validate_and_record(self.b_strategy, LedgerEntry(kind="DEPOSIT", amount=D("200"), occurred_on=day))


class ContextAndManagerTests(TenancyBase):
    def test_no_context_fails_closed(self):
        self.assertIsNone(current_organization_id())
        for model in (Account, StrategyInstance, LedgerEntry):
            with self.assertRaises(NoTenantContext):
                list(model.tenant_objects.all())

    def test_each_tenant_sees_only_its_own_rows_direct_and_through_the_strategy(self):
        with tenant_context(self.a_org):
            self.assertEqual({a.owner_id for a in Account.tenant_objects.all()}, {self.alice.pk})
            self.assertEqual(list(StrategyInstance.tenant_objects.values_list("pk", flat=True)), [self.a_strategy.pk])
            self.assertEqual({e.amount for e in LedgerEntry.tenant_objects.all()}, {D("100.00")})
            self.assertFalse(StrategyInstance.tenant_objects.filter(pk=self.b_strategy.pk).exists())
        with tenant_context(self.b_org):
            self.assertEqual({e.amount for e in LedgerEntry.tenant_objects.all()}, {D("200.00")})
        self.assertIsNone(current_organization_id())

    def test_context_nests_and_always_resets(self):
        with tenant_context(self.a_org):
            with tenant_context(self.b_org):
                self.assertEqual(current_organization_id(), self.b_org.pk)
            self.assertEqual(current_organization_id(), self.a_org.pk)
        with self.assertRaises(RuntimeError):
            with tenant_context(self.a_org):
                raise RuntimeError("boom")
        self.assertIsNone(current_organization_id())

    def test_default_manager_is_unchanged_during_the_dual_scope_stage(self):
        self.assertEqual(StrategyInstance.objects.count(), 2)  # unscoped, as before


class StampingTests(TenancyBase):
    def test_new_rows_are_stamped_with_the_owners_personal_organization(self):
        account = Account.objects.create(owner=self.alice, name="Second", kind="GIA", broker="TRADING212")
        self.assertEqual(account.organization_id, self.a_org.pk)
        self.assertEqual(self.a_strategy.organization_id, self.a_org.pk)

    def test_a_strategy_follows_its_account_and_cannot_mix_organizations(self):
        self.assertEqual(self.a_strategy.organization_id, self.a_strategy.account.organization_id)
        self.a_strategy.organization = self.b_org
        with self.assertRaises(ValidationError):
            self.a_strategy.save()

    def test_update_fields_save_still_stamps(self):
        StrategyInstance.objects.filter(pk=self.a_strategy.pk).update(organization=None)
        row = StrategyInstance.objects.get(pk=self.a_strategy.pk)
        row.name = "Renamed"
        row.save(update_fields=["name"])
        self.assertEqual(StrategyInstance.objects.get(pk=self.a_strategy.pk).organization_id, self.a_org.pk)

    def test_personal_org_is_created_once_with_an_owner_membership(self):
        carol = get_user_model().objects.create_user("carol", password="x" * 12)
        org = personal_org_for(carol)
        self.assertEqual(personal_org_for(carol).pk, org.pk)
        self.assertEqual(Organization.objects.filter(personal_owner=carol).count(), 1)
        self.assertEqual(Membership.objects.get(user=carol, organization=org).role, Membership.OWNER)


class MembershipTests(TenancyBase):
    def test_membership_is_checked_not_assumed(self):
        self.assertIsNotNone(membership_for(self.alice, self.a_org.pk))
        self.assertIsNone(membership_for(self.alice, self.b_org.pk))
        self.assertIsNone(membership_for(self.alice, None))
        self.b_org.status = Organization.SUSPENDED
        self.b_org.save()
        self.assertIsNone(membership_for(self.bob, self.b_org.pk))
        self.assertIsNone(default_membership(self.bob))

    def test_roles_gate_writes(self):
        viewer = Membership(role=Membership.VIEWER)
        self.assertFalse(viewer.can_write)
        self.assertTrue(Membership(role=Membership.MEMBER).can_write)
        self.assertFalse(Membership(role=Membership.MEMBER).can_administer)
        self.assertTrue(Membership(role=Membership.ADMIN).can_administer)


class MiddlewareTests(TenancyBase):
    def test_request_gets_the_users_organization_and_the_context_is_cleared_after(self):
        self.client.force_login(self.alice)
        response = self.client.get(reverse("v2_overview"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.wsgi_request.organization.pk, self.a_org.pk)
        self.assertEqual(response.wsgi_request.membership.role, Membership.OWNER)
        self.assertIsNone(current_organization_id())

    def test_anonymous_request_has_no_organization(self):
        response = self.client.get(reverse("login"))
        self.assertIsNone(response.wsgi_request.organization)

    def test_a_forged_session_value_for_someone_elses_organization_is_ignored(self):
        self.client.force_login(self.alice)
        session = self.client.session
        session[SESSION_KEY] = self.b_org.pk
        session.save()
        response = self.client.get(reverse("v2_overview"))
        self.assertEqual(response.wsgi_request.organization.pk, self.a_org.pk)

    def test_switch_organization_requires_membership(self):
        shared = Organization.objects.create(name="Shared")
        Membership.objects.create(user=self.alice, organization=shared, role=Membership.VIEWER)
        self.client.force_login(self.alice)
        request = self.client.get(reverse("v2_overview")).wsgi_request
        self.assertIsNone(switch_organization(request, self.b_org.pk))
        self.assertEqual(switch_organization(request, shared.pk).role, Membership.VIEWER)
        self.assertEqual(self.client.get(reverse("v2_overview")).wsgi_request.organization.pk, self.a_org.pk,
                         "the switch lived on that request's session object, not a forged cookie")


class BackfillAndCheckTests(TenancyBase):
    def _backfill(self):
        module = importlib.import_module("tracker.migrations.0037_backfill_organizations")
        module.backfill(django_apps, None)

    def test_backfill_restores_unassigned_rows_and_is_idempotent(self):
        for model in (Account, StrategyInstance):
            model.objects.update(organization=None)
        self._backfill()
        self.assertEqual(Account.objects.filter(organization__isnull=True).count(), 0)
        self.assertEqual(StrategyInstance.objects.get(pk=self.a_strategy.pk).organization_id, self.a_org.pk)
        before = list(Organization.objects.values_list("pk", flat=True))
        self._backfill()
        self.assertEqual(list(Organization.objects.values_list("pk", flat=True)), before)
        self.assertEqual(Membership.objects.filter(user=self.alice).count(), 1)

    def test_backfill_creates_an_organization_for_a_user_who_has_none(self):
        dave = get_user_model().objects.create_user("dave", password="x" * 12)
        self.assertFalse(Organization.objects.filter(personal_owner=dave).exists())
        self._backfill()
        self.assertTrue(Organization.objects.filter(personal_owner=dave).exists())

    def test_check_passes_when_clean_and_fails_on_unassigned_or_mismatched_rows(self):
        out = StringIO()
        call_command("tenancy_check", stdout=out)
        self.assertIn("complete and consistent", out.getvalue())
        StrategyInstance.objects.filter(pk=self.a_strategy.pk).update(organization=None)
        with self.assertRaises(CommandError):
            call_command("tenancy_check", stdout=StringIO())
        StrategyInstance.objects.filter(pk=self.a_strategy.pk).update(organization=self.b_org)
        with self.assertRaises(CommandError):
            call_command("tenancy_check", stdout=StringIO())

    def test_ledger_digests_do_not_depend_on_tenancy(self):
        first, second = StringIO(), StringIO()
        call_command("tenancy_check", "--digest", stdout=first)
        for model in (Account, StrategyInstance):
            model.objects.update(organization=None)
        self._backfill()
        call_command("tenancy_check", "--digest", stdout=second)
        self.assertEqual(first.getvalue(), second.getvalue())
        self.assertIn("all ", first.getvalue())
