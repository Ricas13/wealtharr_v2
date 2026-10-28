from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from tracker.models import PlanProviderMapping

from . import gateways
from .models import AuditEvent, Plan, UserAccess

User = get_user_model()
GOOD = "Sturdy-Pass-91xq"


class Base(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("root", "root@x.test", "pw-Admin-1234")
        self.default = Plan.objects.get(is_default=True)
        self.pro = Plan.objects.create(slug="pro", name="Pro", is_public=True, price_label="£5 / month")
        self.client.force_login(self.admin)

    def audit(self, action, **filters):
        return AuditEvent.objects.filter(action=action, **filters)


class AccessTests(Base):
    def test_only_platform_admins_reach_these_pages(self):
        pat = User.objects.create_user("pat", "pat@x.test", GOOD, is_staff=True)
        urls = [reverse("console_users"), reverse("console_user_new"), reverse("console_user", args=[pat.pk]),
                reverse("console_plans"), reverse("console_plan_new"), reverse("console_plan", args=[self.pro.pk])]
        other = self.client_class()
        other.force_login(pat)
        for url in urls:
            self.assertEqual(other.get(url).status_code, 302, url)
            self.assertEqual(self.client_class().get(url).status_code, 302, url)
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.assertEqual(other.post(reverse("console_users_bulk"), {"action": "deactivate", "ids": [self.admin.pk]}).status_code, 302)
        self.assertTrue(User.objects.get(pk=self.admin.pk).is_active)

    def test_sidebar_links_the_native_pages_and_keeps_the_django_admin(self):
        page = self.client.get(reverse("console_users")).content.decode()
        self.assertIn(reverse("console_users"), page)
        self.assertIn(reverse("console_plans"), page)
        self.assertIn(reverse("admin:index"), page)
        self.assertIn(reverse("admin:auth_user_changelist"), page)  # the page's own Advanced link


class UsersList(Base):
    def setUp(self):
        super().setUp()
        for i in range(30):
            User.objects.create_user(f"user{i:02d}", f"user{i:02d}@x.test", GOOD)
        UserAccess.objects.create(user=User.objects.get(username="user03"), plan=self.pro)
        User.objects.filter(username="user04").update(is_active=False)

    def names(self, **params):
        page = self.client.get(reverse("console_users"), params).content.decode()
        return page

    def test_search_filters_and_pagination(self):
        self.assertIn("user03", self.names(q="user03"))
        self.assertNotIn("user05", self.names(q="user03"))
        self.assertIn("user03", self.names(plan=self.pro.pk))
        self.assertNotIn("user05", self.names(plan=self.pro.pk))
        self.assertIn("user05", self.names(plan="default"))
        self.assertNotIn("user03", self.names(plan="default"))
        self.assertIn("user04", self.names(status="inactive"))
        self.assertNotIn("user05", self.names(status="inactive"))
        self.assertIn("root", self.names(role="admin"))
        page1 = self.names()
        self.assertIn("Page 1 of 2", page1)
        self.assertIn("Page 2 of 2", self.names(page=2))
        self.assertIn("31 users", page1)

    def test_bulk_move_to_plan_and_activate_deactivate_with_guards(self):
        a, b = User.objects.get(username="user05"), User.objects.get(username="user06")
        self.client.post(reverse("console_users_bulk"), {"action": f"plan:{self.pro.pk}", "ids": [a.pk, b.pk]})
        self.assertEqual(UserAccess.objects.get(user=a).plan, self.pro)
        self.assertEqual(self.audit("user.plan_assigned", subject_id=a.pk).count(), 1)
        self.client.post(reverse("console_users_bulk"), {"action": "deactivate", "ids": [a.pk, self.admin.pk]})
        self.assertFalse(User.objects.get(pk=a.pk).is_active)
        self.assertTrue(User.objects.get(pk=self.admin.pk).is_active)  # never yourself
        self.client.post(reverse("console_users_bulk"), {"action": "activate", "ids": [a.pk]})
        self.assertTrue(User.objects.get(pk=a.pk).is_active)
        self.assertContains(self.client.post(reverse("console_users_bulk"), {"ids": [a.pk]}, follow=True), "Choose an action")


class UserPages(Base):
    def setUp(self):
        super().setUp()
        self.pat = User.objects.create_user("pat", "pat@x.test", GOOD, first_name="Pat")
        self.url = reverse("console_user", args=[self.pat.pk])

    def test_create_a_user_with_a_plan(self):
        response = self.client.post(reverse("console_user_new"), {
            "username": "newbie", "email": "n@x.test", "first_name": "", "last_name": "", "password1": GOOD,
            "password2": GOOD, "plan": self.pro.pk})
        user = User.objects.get(username="newbie")
        self.assertRedirects(response, reverse("console_user", args=[user.pk]))
        self.assertEqual(UserAccess.objects.get(user=user).plan, self.pro)
        self.assertTrue(user.check_password(GOOD))
        event = self.audit("user.changed", subject_id=user.pk).get()
        self.assertNotIn("password", json_dump(event.metadata))

    def test_profile_edit_is_audited_and_protects_the_last_admin_and_yourself(self):
        self.client.post(self.url, {"form_kind": "profile", "profile-first_name": "Patricia", "profile-last_name": "",
                                    "profile-email": "pat@x.test", "profile-is_active": "on"})
        self.assertEqual(User.objects.get(pk=self.pat.pk).first_name, "Patricia")
        self.assertEqual(self.audit("user.changed", subject_id=self.pat.pk).get().metadata["fields"], ["first_name"])
        me = reverse("console_user", args=[self.admin.pk])
        bad = self.client.post(me, {"form_kind": "profile", "profile-email": "root@x.test", "profile-is_active": "on",
                                    "profile-is_staff": "on"})  # drops platform admin
        self.assertContains(bad, "can&#x27;t take this away from your own account")
        self.assertTrue(User.objects.get(pk=self.admin.pk).is_superuser)
        taken = self.client.post(self.url, {"form_kind": "profile", "profile-email": "root@x.test", "profile-is_active": "on"})
        self.assertContains(taken, "Another account already uses that email")

    def test_plan_and_feature_overrides(self):
        self.client.post(self.url, {"form_kind": "access", "access-plan": self.pro.pk, "access-grant": ["alerts.discord"],
                                    "access-deny": ["alerts.email"], "access-note": "friend"})
        access = UserAccess.objects.get(user=self.pat)
        self.assertEqual((access.plan, access.grant, access.deny, access.note), (self.pro, ["alerts.discord"], ["alerts.email"], "friend"))
        self.assertTrue(self.audit("user.plan_assigned", subject_id=self.pat.pk).exists())
        both = self.client.post(self.url, {"form_kind": "access", "access-plan": self.pro.pk,
                                           "access-grant": ["alerts.email"], "access-deny": ["alerts.email"]})
        self.assertContains(both, "cannot be both granted and denied")

    def test_set_password_signs_them_out_and_is_audited_without_the_value(self):
        victim = self.client_class()
        victim.login(username="pat", password=GOOD)
        self.assertEqual(victim.get("/overview/").status_code, 200)
        self.client.post(self.url, {"form_kind": "password", "password-new_password1": "Another-Pass-77zz",
                                    "password-new_password2": "Another-Pass-77zz"})
        self.assertTrue(User.objects.get(pk=self.pat.pk).check_password("Another-Pass-77zz"))
        self.assertEqual(victim.get("/overview/").status_code, 302)
        self.assertNotIn("Another", json_dump(list(AuditEvent.objects.values_list("metadata", flat=True))))

    def test_reset_email_and_sign_out_everywhere(self):
        sent = []
        with mock.patch("tenancy.accounts.send_system_email", lambda to, subject, body: sent.append(to) or True):
            self.client.post(self.url, {"form_kind": "reset_email"})
        self.assertEqual(sent, ["pat@x.test"])
        self.assertTrue(self.audit("user.reset_email_sent").exists())
        victim = self.client_class()
        victim.login(username="pat", password=GOOD)
        self.client.post(self.url, {"form_kind": "signout"})
        self.assertEqual(victim.get("/overview/").status_code, 302)

    def test_detail_page_shows_billing_activity_and_links_to_the_admin(self):
        page = self.client.get(self.url)
        self.assertContains(page, reverse("admin:auth_user_change", args=[self.pat.pk]))
        self.assertContains(page, "Recent activity")
        self.assertContains(page, "No gateway subscriptions recorded")


class PlanPages(Base):
    def form(self, **over):
        data = {"name": "Starter", "slug": "starter", "description": "Small", "price_label": "£3 / month",
                "sort_order": 1, "is_public": "on", "features": ["alerts.email"], "limit__strategies.max": "2",
                "stripe_price_id": "", "paypal_plan_id": ""}
        data.update(over)
        return data

    def test_create_with_features_a_numeric_limit_and_audit(self):
        response = self.client.post(reverse("console_plan_new"), self.form())
        plan = Plan.objects.get(slug="starter")
        self.assertRedirects(response, reverse("console_plan", args=[plan.pk]))
        self.assertEqual((plan.features, plan.limits, plan.is_public), (["alerts.email"], {"strategies.max": 2}, True))
        self.assertEqual(self.audit("plan.changed", target_id=str(plan.pk)).get().metadata["change"], "created")
        self.client.post(reverse("console_plan", args=[plan.pk]), self.form(**{"limit__strategies.max": ""}))
        self.assertEqual(Plan.objects.get(pk=plan.pk).limits, {})  # blank means no limit

    def test_gateway_ids_are_verified_stored_and_removable(self):
        gateways.save("stripe", {"enabled": True, "restricted_key": "rk_test_c1", "webhook_secret": "whsec_c"})
        with mock.patch("tenancy.gateways.call", return_value=(200, {"active": True, "recurring": {"interval": "month"}})):
            self.client.post(reverse("console_plan", args=[self.pro.pk]), self.form(
                name="Pro", slug="pro", stripe_price_id="price_123"))
        mapping = PlanProviderMapping.objects.get(plan=self.pro, provider="stripe")
        self.assertIsNotNone(mapping.verified_at)
        self.assertContains(self.client.get(reverse("console_plans")), "verified")
        with mock.patch("tenancy.gateways.call", return_value=(200, {"active": False, "recurring": {}})):
            self.client.post(reverse("console_plan", args=[self.pro.pk]), self.form(
                name="Pro", slug="pro", stripe_price_id="price_999"))
        self.assertIsNone(PlanProviderMapping.objects.get(plan=self.pro, provider="stripe").verified_at)
        self.client.post(reverse("console_plan", args=[self.pro.pk]), self.form(name="Pro", slug="pro"))
        self.assertFalse(PlanProviderMapping.objects.filter(plan=self.pro).exists())
        bad = self.client.post(reverse("console_plan", args=[self.pro.pk]), self.form(
            name="Pro", slug="pro", stripe_price_id="price_1/../x"))
        self.assertContains(bad, "doesn&#x27;t look like a valid id")

    def test_default_plan_cannot_be_unticked_or_deleted_and_used_plans_cannot_be_deleted(self):
        bad = self.client.post(reverse("console_plan", args=[self.default.pk]), self.form(
            name="Full", slug="full", all_features="on"))
        self.assertContains(bad, "There must always be a default plan")
        self.client.post(reverse("console_plan_delete", args=[self.default.pk]))
        self.assertTrue(Plan.objects.filter(pk=self.default.pk).exists())
        UserAccess.objects.create(user=User.objects.create_user("pat", password=GOOD), plan=self.pro)
        self.client.post(reverse("console_plan_delete", args=[self.pro.pk]))
        self.assertTrue(Plan.objects.filter(pk=self.pro.pk).exists())
        spare = Plan.objects.create(slug="spare", name="Spare")
        self.client.post(reverse("console_plan_delete", args=[spare.pk]))
        self.assertFalse(Plan.objects.filter(pk=spare.pk).exists())
        self.assertEqual(self.audit("plan.changed", target_id=str(spare.pk)).get().metadata["change"], "deleted")

    def test_list_counts_users_including_those_on_the_default(self):
        User.objects.create_user("a", password=GOOD)
        User.objects.create_user("b", password=GOOD)
        page = self.client.get(reverse("console_plans")).content.decode()
        self.assertIn("Pro", page)
        self.assertIn(reverse("control") + "#stripe", page)
        self.assertIn(reverse("admin:tenancy_plan_changelist"), page)


def json_dump(value):
    import json
    return json.dumps(value, default=str)
