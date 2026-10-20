"""Phase 1 routes and security: no sample financial figures or cross-owner data."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Account, StrategyInstance, StrategyPreset


class NewShellTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user("v2owner", password="valid-test-password-12345")
        self.other = User.objects.create_user("v2other", password="valid-test-password-12345")
        self.preset, _ = StrategyPreset.objects.get_or_create(
            slug="nasdaq-quarterly-target",
            defaults={"name": "9Sig", "summary": "Fixed rules", "risk": "HIGH", "active": True},
        )
        a = Account.objects.create(
            owner=self.owner, name="My ISA", kind="ISA", broker="TRADING212"
        )
        self.mine = StrategyInstance.objects.create(
            owner=self.owner, account=a, preset=self.preset, name="Owner-only strategy"
        )
        b = Account.objects.create(
            owner=self.other, name="Other ISA", kind="ISA", broker="TRADING212"
        )
        self.theirs = StrategyInstance.objects.create(
            owner=self.other, account=b, preset=self.preset, name="SECRET OTHER STRATEGY"
        )

    def test_all_new_shell_pages_require_login(self):
        routes = [
            reverse("v2_overview"),
            reverse("v2_catalogue"),
            reverse("v2_settings"),
            reverse("v2_add_strategy"),
        ] + [
            reverse("v2_instance_page", args=[self.mine.pk, part])
            for part in ("today", "history", "ledger", "strategy", "breakdown")
        ]
        for url in routes:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn("/login/", response["Location"])

    def test_sidebar_is_owner_scoped_and_uses_real_instances(self):
        self.client.force_login(self.owner)
        for url in (
            reverse("v2_overview"),
            reverse("v2_catalogue"),
            reverse("v2_settings"),
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Owner-only strategy")
                self.assertNotContains(response, "SECRET OTHER STRATEGY")
                self.assertContains(response, 'id="v2-sidebar"')
                self.assertContains(response, 'href="#v2-main"')
                self.assertContains(response, 'aria-label="Primary navigation"')

    def test_instance_pages_have_expected_urls_active_navigation_and_no_fake_values(self):
        self.client.force_login(self.owner)
        for part, title in (
            ("today", "Today"), ("history", "History"),
            ("ledger", "Ledger"), ("strategy", "Strategy"),
            ("breakdown", "Breakdown"),
        ):
            with self.subTest(part=part):
                url = reverse("v2_instance_page", args=[self.mine.pk, part])
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, f"<h1>{title}</h1>", html=True)
                self.assertContains(response, 'aria-current="page"')
                self.assertContains(response, "Owner-only strategy")
                self.assertNotContains(response, "£14,820")
                self.assertNotContains(response, "£12,400")

    def test_other_owner_and_unsupported_routes_are_refused(self):
        self.client.force_login(self.owner)
        url = reverse("v2_instance_page", args=[self.theirs.pk, "today"])
        self.assertEqual(self.client.get(url).status_code, 404)
        url = reverse("v2_instance_page", args=[self.mine.pk, "unknown"])
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_empty_user_sees_no_extraneous_owner_data(self):
        new_user = get_user_model().objects.create_user(
            "newv2", password="valid-test-password-12345"
        )
        self.client.force_login(new_user)
        response = self.client.get(reverse("v2_overview"))
        self.assertContains(response, "No strategies yet")
        self.assertNotContains(response, "Owner-only strategy")
        self.assertNotContains(response, "SECRET OTHER STRATEGY")
