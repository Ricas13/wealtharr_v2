"""Add strategy shows the hypothetical comparison, with the strategies' own names, before setup."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from tracker.test_study import TODAY, seed


class AddStrategyCompareTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user("owner", password="a-long-test-password-1")
        self.client.force_login(self.user)

    def seed_history(self):
        from tracker.test_study_fixed import seed_all_weather
        start = TODAY - timedelta(days=800)
        seed("QQQ", start, lambda d, i: 100 * (1.001 ** i))
        seed_all_weather(start, start)

    def test_add_page_shows_the_comparison_with_real_names(self):
        self.seed_history()
        page = self.client.get(reverse("v2_add_strategy"))
        self.assertContains(page, "How each strategy did in the past")
        self.assertContains(page, 'data-compare-url="%s"' % reverse("v2_compare_json"))
        self.assertContains(page, "js/compare.js")
        self.assertContains(page, "css/compare.css")
        self.assertContains(page, "9Sig")
        self.assertContains(page, "All Seasons")
        self.assertContains(page, "Hypothetical, not anyone")
        # The three setup choices come first, side by side; the comparison sits below them.
        body = page.content.decode()
        self.assertEqual(body.count("w2-panel w2-wizard-card\""), 3)
        self.assertLess(body.index('name="strategy"'), body.index("How each strategy did in the past"))
        self.assertLess(body.index("Create strategy"), body.index("How each strategy did in the past"))

    def test_named_endpoint_needs_sign_in_and_uses_real_names(self):
        self.seed_history()
        data = self.client.get(reverse("v2_compare_json") + "?amount=1000&monthly=0&range=1y").json()
        labels = [r["label"] for r in data["rows"]]
        self.assertIn("9Sig", labels)
        self.assertIn("All Seasons", labels)
        self.assertNotIn("All-season mix", labels)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("v2_compare_json")).status_code, 302)

    def test_public_homepage_keeps_generic_names(self):
        self.seed_history()
        self.client.logout()
        data = self.client.get("/compare.json?amount=1000&monthly=0&range=1y").json()
        labels = [r["label"] for r in data["rows"]]
        self.assertIn("Quarterly signal", labels)
        self.assertIn("All-season mix", labels)
        page = self.client.get("/?range=1y")
        self.assertNotContains(page, "9Sig")
        self.assertContains(page, "js/compare.js")

    def test_other_pages_do_not_load_the_comparison(self):
        page = self.client.get(reverse("v2_overview"))
        self.assertNotContains(page, "js/compare.js")
