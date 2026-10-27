"""Installable app: the worker stores static files only, the offline page is generic."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class ServiceWorkerTests(TestCase):
    def test_worker_is_served_from_the_root_with_no_caching_of_itself(self):
        response = self.client.get("/sw.js")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/javascript")
        self.assertEqual(response["Service-Worker-Allowed"], "/")
        self.assertEqual(response["Cache-Control"], "no-cache")

    def test_worker_never_caches_pages_or_data(self):
        source = self.client.get("/sw.js").content.decode()
        # Only static files and the one offline page are ever written to a cache.
        self.assertEqual(source.count("cache.put("), 1)
        self.assertIn('url.pathname.startsWith("/static/")', source)
        self.assertIn('request.method !== "GET"', source)
        self.assertIn("url.origin !== self.location.origin", source)
        self.assertIn("cache.add(OFFLINE)", source)

    def test_worker_version_follows_its_code(self):
        from tracker import pwa
        first = self.client.get("/sw.js").content
        self.assertIn(pwa._version().encode(), first)
        self.assertNotIn(b"%(version)s", first)

    def test_offline_page_is_public_and_holds_no_figures(self):
        response = self.client.get(reverse("offline"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "You're offline")
        self.assertNotContains(response, "£")


class ShellMarkupTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("pwa-owner", password="test-strong-1")
        self.client.force_login(self.user)

    def test_signed_in_pages_link_the_manifest_and_carry_quick_search(self):
        page = self.client.get(reverse("v2_overview"))
        self.assertContains(page, 'rel="manifest"')
        self.assertContains(page, 'name="theme-color"')
        self.assertContains(page, 'id="v2-palette"')
        self.assertContains(page, "Add strategy")

    def test_quick_search_lists_only_the_owners_own_strategies(self):
        from tracker.models import Account, StrategyInstance, StrategyPreset
        preset, _ = StrategyPreset.objects.get_or_create(
            slug="nasdaq-quarterly-target", defaults={"name": "9Sig", "active": True})
        other = get_user_model().objects.create_user("pwa-other", password="test-strong-1")
        for owner, name in ((self.user, "Mine ISA strategy"), (other, "SECRET_OTHER_STRATEGY")):
            account = Account.objects.create(owner=owner, name=name, kind="ISA", broker="TRADING212")
            StrategyInstance.objects.create(owner=owner, account=account, preset=preset, name=name)
        page = self.client.get(reverse("v2_overview")).content.decode()
        self.assertIn("Mine ISA strategy · Ledger", page)
        self.assertNotIn("SECRET_OTHER_STRATEGY", page)
