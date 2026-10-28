"""Two tenants, every strategy route: tenant B's objects are never reachable by tenant A.

New routes that take a strategy id must be added to ROUTES, or the completeness test fails.
"""
from datetime import timedelta
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import get_resolver, reverse
from django.utils import timezone

from tenancy.context import tenant_context
from tracker import v2_urls
from tracker.models import LedgerEntry, StrategySignal
from tracker.services import validate_and_record
from tracker.test_tenancy_core import _strategy
from tracker.v2_views import LABELS

PASSWORD = "isolation-test-password-1"


def routes(strategy, entry, signal):
    """(url name, kwargs) for every route that names a strategy, entry or signal of B."""
    sid = strategy.pk
    rows = [
        ("v2_study_window_json", {"instance_id": sid}),
        ("v2_study_simulate_json", {"instance_id": sid}),
        ("v2_go_live", {"instance_id": sid}),
        ("v2_strategy_plan", {"instance_id": sid}),
        ("v2_ledger_add", {"instance_id": sid}),
        ("v2_ledger_modify", {"instance_id": sid, "entry_id": entry.pk, "operation": "edit"}),
        ("v2_guided_review", {"instance_id": sid, "signal_id": signal.pk}),
        ("v2_notice_feedback", {"instance_id": sid, "signal_id": signal.pk}),
    ]
    rows += [("v2_instance_page", {"instance_id": sid, "section": section}) for section in LABELS]
    return rows


ROUTE_NAMES = {
    "v2_study_window_json", "v2_study_simulate_json", "v2_go_live", "v2_strategy_plan", "v2_ledger_add",
    "v2_ledger_modify", "v2_guided_review", "v2_notice_feedback", "v2_instance_page",
}


class TwoTenantBase(TestCase):
    def setUp(self):
        User = get_user_model()
        self.alice = User.objects.create_user("alice", password=PASSWORD)
        self.bob = User.objects.create_user("bob", password=PASSWORD)
        self.a_strategy = _strategy(self.alice, "ALICE_STRATEGY")
        self.b_strategy = _strategy(self.bob, "BOB_PRIVATE_STRATEGY")
        day = timezone.localdate() - timedelta(days=4)
        validate_and_record(self.a_strategy, LedgerEntry(kind="DEPOSIT", amount=D("111"), occurred_on=day))
        self.b_entry = validate_and_record(
            self.b_strategy, LedgerEntry(kind="DEPOSIT", amount=D("987654"), occurred_on=day))
        self.b_signal = StrategySignal.objects.create(
            strategy=self.b_strategy, as_of=day, kind="QUARTERLY", title="BOB_SIGNAL",
            signal_key="bob-1", message="m", actions=[])
        self.client.force_login(self.alice)


class RouteIsolationTests(TwoTenantBase):
    def test_every_route_covers_the_strategy_routes_in_v2_urls(self):
        named = {p.name for p in v2_urls.urlpatterns
                 if any(token in str(p.pattern) for token in ("instance_id", "signal_id", "entry_id"))}
        self.assertEqual(named, ROUTE_NAMES,
                         "a route that takes a strategy id is not in the isolation test: add it to ROUTES")

    def test_tenant_a_cannot_reach_any_of_tenant_bs_objects(self):
        for name, kwargs in routes(self.b_strategy, self.b_entry, self.b_signal):
            url = reverse(name, kwargs=kwargs)
            statuses = {self.client.get(url).status_code, self.client.post(url, {}).status_code}
            self.assertTrue(statuses <= {404, 405} and 404 in statuses, (name, url, statuses))

    def test_the_same_routes_work_for_the_owner_of_the_strategy(self):
        page = self.client.get(reverse("v2_instance_page", args=[self.a_strategy.pk, "today"]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "ALICE_STRATEGY")

    def test_tenant_a_pages_never_contain_tenant_bs_data(self):
        for url in (reverse("v2_overview"), reverse("v2_settings"),
                    reverse("v2_instance_page", args=[self.a_strategy.pk, "today"]),
                    reverse("v2_instance_page", args=[self.a_strategy.pk, "ledger"])):
            body = self.client.get(url).content.decode()
            for secret in ("BOB_PRIVATE_STRATEGY", "BOB_SIGNAL", "987,654"):
                self.assertNotIn(secret, body, (url, secret))

    def test_the_tenant_manager_scopes_by_user_on_its_own(self):
        """The manager is a second wall: it hides B's rows even without the explicit owner filter."""
        with tenant_context(self.alice):
            self.assertFalse(type(self.b_strategy).tenant_objects.filter(pk=self.b_strategy.pk).exists())
            self.assertFalse(LedgerEntry.tenant_objects.filter(pk=self.b_entry.pk).exists())
            self.assertTrue(type(self.a_strategy).tenant_objects.filter(pk=self.a_strategy.pk).exists())
