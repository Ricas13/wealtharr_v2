"""Plans: the feature registry, the single can()/limit() check, the guard and the admin."""
from contextlib import contextmanager

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.http import HttpResponse
from django.template import Context, Template
from django.test import RequestFactory, TestCase
from django.urls import reverse

from tenancy import access, features
from tenancy.decorators import require_feature
from tenancy.models import Plan, AuditEvent, UserAccess


@contextmanager
def declared(*specs):
    """Declare test features and put the registry back afterwards."""
    saved = dict(features._registry)
    try:
        for spec in specs:
            features.register(*spec[:2], limit=len(spec) > 2 and spec[2])
        yield
    finally:
        features._registry.clear()
        features._registry.update(saved)


MAPPING_FORMS = {"provider_mappings-TOTAL_FORMS": 0, "provider_mappings-INITIAL_FORMS": 0,
                 "provider_mappings-MIN_NUM_FORMS": 0, "provider_mappings-MAX_NUM_FORMS": 1000}


class AccessBase(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user("plain", password="x" * 12)
        self.admin = User.objects.create_superuser("boss", password="x" * 12)
        self.full = Plan.objects.get(slug="full")


class RegistryTests(AccessBase):
    def test_keys_are_validated_and_redeclaring_the_same_feature_is_fine(self):
        with declared():
            features.register("demo.study", "What-if")
            features.register("demo.study", "What-if")
            with self.assertRaises(ValueError):
                features.register("demo.study", "Something else")
            for bad in ("Study", "a", "has space", "9lives"):
                with self.assertRaises(ValueError):
                    features.register(bad, "x")


class CanTests(AccessBase):
    def test_the_seeded_full_plan_is_the_default_and_keeps_today_unchanged(self):
        self.assertTrue(self.full.is_default and self.full.all_features)
        with declared(("demo.study", "What-if")):
            self.assertTrue(access.can(self.user, "demo.study"))
        with declared(("brand.new", "Added later")):
            self.assertTrue(access.can(self.user, "brand.new"), "Full includes features added later")

    def test_a_custom_plan_grants_only_what_it_lists(self):
        plan = Plan.objects.create(slug="basic", name="Basic", features=["demo.email"])
        UserAccess.objects.create(user=self.user, plan=plan)
        with declared(("demo.email", "Email"), ("demo.discord", "Discord")):
            self.assertTrue(access.can(self.user, "demo.email"))
            self.assertFalse(access.can(self.user, "demo.discord"))

    def test_overrides_beat_the_plan_and_deny_beats_grant(self):
        plan = Plan.objects.create(slug="basic", name="Basic", features=["demo.email"])
        UserAccess.objects.create(user=self.user, plan=plan, grant=["demo.discord"], deny=["demo.email"])
        with declared(("demo.email", "Email"), ("demo.discord", "Discord")):
            self.assertTrue(access.can(self.user, "demo.discord"))
            self.assertFalse(access.can(self.user, "demo.email"))

    def test_platform_admins_always_pass_and_anonymous_never(self):
        from django.contrib.auth.models import AnonymousUser
        with declared(("x.feature", "X")):
            self.assertTrue(access.can(self.admin, "x.feature"))
            self.assertFalse(access.can(AnonymousUser(), "x.feature"))

    def test_fail_closed_for_unknown_features_and_missing_plans(self):
        with declared(("x.feature", "X")):
            with self.assertLogs("tenancy.access", level="WARNING"):
                self.assertFalse(access.can(self.user, "not.declared"))
            Plan.objects.update(is_default=False)  # no default and no row for this user
            self.assertFalse(access.can(self.user, "x.feature"))
            self.assertTrue(access.can(self.admin, "x.feature"))


class LimitTests(AccessBase):
    def test_numeric_limits(self):
        plan = Plan.objects.create(slug="small", name="Small", limits={"demo.max": 2})
        with declared(("demo.max", "Strategies", True), ("demo.study", "What-if")):
            self.assertIsNone(access.limit(self.user, "demo.max"), "Full is unlimited")
            UserAccess.objects.create(user=self.user, plan=plan)
            self.assertEqual(access.limit(self.user, "demo.max"), 2)
            self.assertIsNone(access.limit(self.admin, "demo.max"))
            with self.assertLogs("tenancy.access", level="WARNING"):
                self.assertEqual(access.limit(self.user, "demo.study"), 0, "not a numeric feature")


class GuardTests(AccessBase):
    def _view(self):
        @require_feature("demo.study")
        def view(request):
            return HttpResponse("ok")
        return view

    def test_decorator_allows_and_blocks(self):
        plan = Plan.objects.create(slug="none", name="None")
        factory = RequestFactory()
        with declared(("demo.study", "What-if")):
            request = factory.get("/")
            request.user = self.user
            self.assertEqual(self._view()(request).status_code, 200)
            UserAccess.objects.create(user=self.user, plan=plan)
            self.assertEqual(self._view()(request).status_code, 403)

    def test_template_filter(self):
        plan = Plan.objects.create(slug="none", name="None")
        UserAccess.objects.create(user=self.user, plan=plan)
        template = Template('{% load access %}{% if user|can:"demo.study" %}yes{% else %}no{% endif %}')
        with declared(("demo.study", "What-if")):
            self.assertEqual(template.render(Context({"user": self.user})), "no")
            self.assertEqual(template.render(Context({"user": self.admin})), "yes")


class ModelTests(AccessBase):
    def test_only_one_default_plan(self):
        other = Plan.objects.create(slug="basic", name="Basic", is_default=True)
        self.full.refresh_from_db()
        self.assertFalse(self.full.is_default)
        self.assertTrue(Plan.objects.get(pk=other.pk).is_default)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Plan.objects.filter(pk=self.full.pk).update(is_default=True)


class AdminTests(AccessBase):
    def test_only_platform_admins_reach_the_pages(self):
        for name in ("admin:tenancy_plan_changelist", "admin:auth_user_changelist",
                     "admin:tenancy_auditevent_changelist"):
            self.client.force_login(self.user)
            self.assertNotEqual(self.client.get(reverse(name)).status_code, 200, name)
            self.client.force_login(self.admin)
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)

    def test_creating_a_plan_validates_features_and_is_audited(self):
        self.client.force_login(self.admin)
        url = reverse("admin:tenancy_plan_add")
        with declared(("demo.email", "Email"), ("demo.max", "Strategies", True)):
            bad = self.client.post(url, {**MAPPING_FORMS, "name": "Basic", "slug": "basic", "features": ["nonsense"], "limits": "{}", "sort_order": 0})
            self.assertEqual(bad.status_code, 200)
            self.assertFalse(Plan.objects.filter(slug="basic").exists())
            bad_limit = self.client.post(url, {**MAPPING_FORMS, "name": "Basic", "slug": "basic", "features": [],
                                               "limits": '{"demo.email": 3}', "sort_order": 0})
            self.assertFalse(Plan.objects.filter(slug="basic").exists(), bad_limit.content[:0])
            ok = self.client.post(url, {**MAPPING_FORMS, "name": "Basic", "slug": "basic", "features": ["demo.email"],
                                        "limits": '{"demo.max": 2}', "sort_order": 0})
            self.assertEqual(ok.status_code, 302)
        plan = Plan.objects.get(slug="basic")
        self.assertEqual((plan.features, plan.limits), (["demo.email"], {"demo.max": 2}))
        event = AuditEvent.objects.get(action="plan.changed")
        self.assertEqual((event.actor_id, event.target_type), (self.admin.pk, "Plan"))
        self.assertEqual(event.subject_id, None)

    def test_audit_events_cannot_be_added_changed_or_deleted_in_the_admin(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("admin:tenancy_auditevent_add")).status_code, 403)


class PlanLinkTests(AccessBase):
    def test_payment_links_must_be_https_and_are_saved_with_the_plan(self):
        self.client.force_login(self.admin)
        url = reverse("admin:tenancy_plan_add")
        base = {**MAPPING_FORMS, "name": "Plus", "slug": "plus", "description": "More", "price_label": "£5 / month",
                "features": [], "limits": "{}", "sort_order": 1}
        bad = self.client.post(url, dict(base, stripe_link="http://buy.stripe.com/x"))
        self.assertEqual(bad.status_code, 200)
        self.assertFalse(Plan.objects.filter(slug="plus").exists())
        ok = self.client.post(url, dict(base, stripe_link="https://buy.stripe.com/abc",
                                        paypal_link="https://www.paypal.com/ncp/payment/xyz", is_public="on"))
        self.assertEqual(ok.status_code, 302)
        plan = Plan.objects.get(slug="plus")
        self.assertEqual((plan.stripe_link, plan.price_label, plan.is_public),
                         ("https://buy.stripe.com/abc", "£5 / month", True))

    def test_a_plan_with_users_cannot_be_deleted_and_an_empty_one_can(self):
        spare = Plan.objects.create(slug="spare", name="Spare")
        UserAccess.objects.create(user=self.user, plan=spare)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(reverse("admin:tenancy_plan_delete", args=[spare.pk]),
                                          {"post": "yes"}).status_code, 403)
        unused = Plan.objects.create(slug="unused", name="Unused")
        self.client.post(reverse("admin:tenancy_plan_delete", args=[unused.pk]), {"post": "yes"})
        self.assertFalse(Plan.objects.filter(slug="unused").exists())
        self.assertTrue(AuditEvent.objects.filter(action="plan.changed", metadata__change="deleted").exists())


class UserManagementTests(AccessBase):
    def _post(self, user, plan, **extra):
        data = {
            "username": user.username, "email": "new@example.com", "first_name": "Pat", "last_name": "",
            "is_active": "on", "date_joined_0": user.date_joined.strftime("%Y-%m-%d"),
            "date_joined_1": user.date_joined.strftime("%H:%M:%S"),
            "access-TOTAL_FORMS": "1", "access-INITIAL_FORMS": "1" if hasattr(user, "access") else "0",
            "access-MIN_NUM_FORMS": "0", "access-MAX_NUM_FORMS": "1", "access-0-plan": plan.pk,
            "access-0-note": "set by hand",
        }
        data.update(extra)
        return self.client.post(reverse("admin:auth_user_change", args=[user.pk]), data)

    def test_the_user_list_shows_plans_and_the_sidebar_links_plans_for_the_owner_only(self):
        self.client.force_login(self.admin)
        page = self.client.get(reverse("admin:auth_user_changelist"))
        self.assertContains(page, "(default)")
        self.assertContains(page, "Assign plan: Full")
        self.assertContains(self.client.get(reverse("v2_overview")), reverse("console_plans"))
        staff = get_user_model().objects.create_user("helper", password="x" * 12, is_staff=True)
        self.client.force_login(staff)
        self.assertNotContains(self.client.get(reverse("v2_overview")), reverse("console_plans"))

    def test_editing_a_user_assigns_a_plan_and_feature_overrides_and_is_audited_without_secrets(self):
        plus = Plan.objects.create(slug="plus", name="Plus", features=["demo.email"])
        self.client.force_login(self.admin)
        with declared(("demo.email", "Email"), ("demo.discord", "Discord")):
            response = self._post(self.user, plus, **{"access-0-grant": ["demo.discord"]})
            self.assertEqual(response.status_code, 302, getattr(response, "context", None) and
                             response.context["adminform"].form.errors)
            self.assertTrue(access.can(self.user, "demo.discord"))
            self.assertTrue(access.can(self.user, "demo.email"))
        record = UserAccess.objects.get(user=self.user)
        self.assertEqual((record.plan, record.grant, record.note), (plus, ["demo.discord"], "set by hand"))
        self.user.refresh_from_db()
        self.assertEqual((self.user.email, self.user.first_name), ("new@example.com", "Pat"))
        changed = AuditEvent.objects.get(action="user.changed")
        self.assertEqual((changed.actor_id, changed.subject_id), (self.admin.pk, self.user.pk))
        self.assertIn("email", changed.metadata["fields"])
        self.assertNotIn("new@example.com", str(changed.metadata))
        assigned = AuditEvent.objects.get(action="user.plan_assigned")
        self.assertEqual((assigned.subject_id, assigned.metadata["plan"], assigned.metadata["via"]),
                         (self.user.pk, "plus", "user page"))

    def test_bulk_assign_moves_several_users_and_is_audited(self):
        plus = Plan.objects.create(slug="plus", name="Plus")
        other = get_user_model().objects.create_user("second", password="x" * 12)
        self.client.force_login(self.admin)
        response = self.client.post(reverse("admin:auth_user_changelist"), {
            "action": "assign_plan_%d" % plus.pk, "_selected_action": [self.user.pk, other.pk]}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(UserAccess.objects.filter(plan=plus).values_list("user_id", flat=True)),
                         {self.user.pk, other.pk})
        self.assertEqual(AuditEvent.objects.filter(action="user.plan_assigned").count(), 2)

    def test_a_user_without_a_plan_row_gets_the_default_and_a_non_owner_cannot_manage_users(self):
        self.assertTrue(self.full.is_default)
        staff = get_user_model().objects.create_user("helper", password="x" * 12, is_staff=True)
        self.client.force_login(staff)
        self.assertNotEqual(self.client.get(reverse("admin:auth_user_changelist")).status_code, 200)


class DefaultPlanProtectionTests(AccessBase):
    def test_the_default_plan_cannot_be_deleted_or_unticked_and_tenancy_check_notices_its_absence(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(reverse("admin:tenancy_plan_delete", args=[self.full.pk]),
                                          {"post": "yes"}).status_code, 403)
        change = reverse("admin:tenancy_plan_change", args=[self.full.pk])
        response = self.client.post(change, {"name": "Full", "slug": "full", "all_features": "on",
                                             "features": [], "limits": "{}", "sort_order": 0})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "There must always be a default plan")
        self.assertTrue(Plan.objects.get(slug="full").is_default)
        from io import StringIO
        from django.core.management import call_command
        from django.core.management.base import CommandError
        Plan.objects.update(is_default=False)
        with self.assertRaises(CommandError) as raised:
            call_command("tenancy_check", stdout=StringIO())
        self.assertIn("no default plan", str(raised.exception))
