"""Access levels: the feature registry, the single can()/limit() check, the guard and the admin."""
from contextlib import contextmanager

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.http import HttpResponse
from django.template import Context, Template
from django.test import RequestFactory, TestCase
from django.urls import reverse

from tenancy import access, features
from tenancy.decorators import require_feature
from tenancy.models import AccessLevel, AuditEvent, UserAccess


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


class AccessBase(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user("plain", password="x" * 12)
        self.admin = User.objects.create_superuser("boss", password="x" * 12)
        self.full = AccessLevel.objects.get(slug="full")


class RegistryTests(AccessBase):
    def test_keys_are_validated_and_redeclaring_the_same_feature_is_fine(self):
        with declared():
            features.register("study.simulate", "What-if")
            features.register("study.simulate", "What-if")
            with self.assertRaises(ValueError):
                features.register("study.simulate", "Something else")
            for bad in ("Study", "a", "has space", "9lives"):
                with self.assertRaises(ValueError):
                    features.register(bad, "x")


class CanTests(AccessBase):
    def test_the_seeded_full_level_is_the_default_and_keeps_today_unchanged(self):
        self.assertTrue(self.full.is_default and self.full.all_features)
        with declared(("study.simulate", "What-if")):
            self.assertTrue(access.can(self.user, "study.simulate"))
        with declared(("brand.new", "Added later")):
            self.assertTrue(access.can(self.user, "brand.new"), "Full includes features added later")

    def test_a_custom_level_grants_only_what_it_lists(self):
        level = AccessLevel.objects.create(slug="basic", name="Basic", features=["alerts.email"])
        UserAccess.objects.create(user=self.user, level=level)
        with declared(("alerts.email", "Email"), ("alerts.discord", "Discord")):
            self.assertTrue(access.can(self.user, "alerts.email"))
            self.assertFalse(access.can(self.user, "alerts.discord"))

    def test_overrides_beat_the_level_and_deny_beats_grant(self):
        level = AccessLevel.objects.create(slug="basic", name="Basic", features=["alerts.email"])
        UserAccess.objects.create(user=self.user, level=level, grant=["alerts.discord"], deny=["alerts.email"])
        with declared(("alerts.email", "Email"), ("alerts.discord", "Discord")):
            self.assertTrue(access.can(self.user, "alerts.discord"))
            self.assertFalse(access.can(self.user, "alerts.email"))

    def test_platform_admins_always_pass_and_anonymous_never(self):
        from django.contrib.auth.models import AnonymousUser
        with declared(("x.feature", "X")):
            self.assertTrue(access.can(self.admin, "x.feature"))
            self.assertFalse(access.can(AnonymousUser(), "x.feature"))

    def test_fail_closed_for_unknown_features_and_missing_levels(self):
        with declared(("x.feature", "X")):
            with self.assertLogs("tenancy.access", level="WARNING"):
                self.assertFalse(access.can(self.user, "not.declared"))
            AccessLevel.objects.update(is_default=False)  # no default and no row for this user
            self.assertFalse(access.can(self.user, "x.feature"))
            self.assertTrue(access.can(self.admin, "x.feature"))


class LimitTests(AccessBase):
    def test_numeric_limits(self):
        level = AccessLevel.objects.create(slug="small", name="Small", limits={"strategies.max": 2})
        with declared(("strategies.max", "Strategies", True), ("study.simulate", "What-if")):
            self.assertIsNone(access.limit(self.user, "strategies.max"), "Full is unlimited")
            UserAccess.objects.create(user=self.user, level=level)
            self.assertEqual(access.limit(self.user, "strategies.max"), 2)
            self.assertIsNone(access.limit(self.admin, "strategies.max"))
            with self.assertLogs("tenancy.access", level="WARNING"):
                self.assertEqual(access.limit(self.user, "study.simulate"), 0, "not a numeric feature")


class GuardTests(AccessBase):
    def _view(self):
        @require_feature("study.simulate")
        def view(request):
            return HttpResponse("ok")
        return view

    def test_decorator_allows_and_blocks(self):
        level = AccessLevel.objects.create(slug="none", name="None")
        factory = RequestFactory()
        with declared(("study.simulate", "What-if")):
            request = factory.get("/")
            request.user = self.user
            self.assertEqual(self._view()(request).status_code, 200)
            UserAccess.objects.create(user=self.user, level=level)
            self.assertEqual(self._view()(request).status_code, 403)

    def test_template_filter(self):
        level = AccessLevel.objects.create(slug="none", name="None")
        UserAccess.objects.create(user=self.user, level=level)
        template = Template('{% load access %}{% if user|can:"study.simulate" %}yes{% else %}no{% endif %}')
        with declared(("study.simulate", "What-if")):
            self.assertEqual(template.render(Context({"user": self.user})), "no")
            self.assertEqual(template.render(Context({"user": self.admin})), "yes")


class ModelTests(AccessBase):
    def test_only_one_default_level(self):
        other = AccessLevel.objects.create(slug="basic", name="Basic", is_default=True)
        self.full.refresh_from_db()
        self.assertFalse(self.full.is_default)
        self.assertTrue(AccessLevel.objects.get(pk=other.pk).is_default)
        with self.assertRaises(IntegrityError), transaction.atomic():
            AccessLevel.objects.filter(pk=self.full.pk).update(is_default=True)


class AdminTests(AccessBase):
    def test_only_platform_admins_reach_the_pages(self):
        for name in ("admin:tenancy_accesslevel_changelist", "admin:tenancy_useraccess_changelist",
                     "admin:tenancy_auditevent_changelist"):
            self.client.force_login(self.user)
            self.assertNotEqual(self.client.get(reverse(name)).status_code, 200, name)
            self.client.force_login(self.admin)
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)

    def test_creating_a_level_validates_features_and_is_audited(self):
        self.client.force_login(self.admin)
        url = reverse("admin:tenancy_accesslevel_add")
        with declared(("alerts.email", "Email"), ("strategies.max", "Strategies", True)):
            bad = self.client.post(url, {"name": "Basic", "slug": "basic", "features": ["nonsense"], "limits": "{}"})
            self.assertEqual(bad.status_code, 200)
            self.assertFalse(AccessLevel.objects.filter(slug="basic").exists())
            bad_limit = self.client.post(url, {"name": "Basic", "slug": "basic", "features": [],
                                               "limits": '{"alerts.email": 3}'})
            self.assertFalse(AccessLevel.objects.filter(slug="basic").exists(), bad_limit.content[:0])
            ok = self.client.post(url, {"name": "Basic", "slug": "basic", "features": ["alerts.email"],
                                        "limits": '{"strategies.max": 2}'})
            self.assertEqual(ok.status_code, 302)
        level = AccessLevel.objects.get(slug="basic")
        self.assertEqual((level.features, level.limits), (["alerts.email"], {"strategies.max": 2}))
        event = AuditEvent.objects.get(action="access.level_changed")
        self.assertEqual((event.actor_id, event.target_type), (self.admin.pk, "AccessLevel"))
        self.assertEqual(event.subject_id, None)

    def test_assigning_a_user_a_level_is_audited_against_that_user(self):
        level = AccessLevel.objects.create(slug="basic", name="Basic")
        self.client.force_login(self.admin)
        with declared(("alerts.email", "Email")):
            response = self.client.post(reverse("admin:tenancy_useraccess_add"), {
                "user": self.user.pk, "level": level.pk, "grant": ["alerts.email"], "deny": [], "note": "beta"})
        self.assertEqual(response.status_code, 302)
        event = AuditEvent.objects.get(action="access.user_changed")
        self.assertEqual((event.actor_id, event.subject_id), (self.admin.pk, self.user.pk))
        self.assertTrue(UserAccess.objects.get(user=self.user).grant == ["alerts.email"])

    def test_audit_events_cannot_be_added_changed_or_deleted_in_the_admin(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("admin:tenancy_auditevent_add")).status_code, 403)
