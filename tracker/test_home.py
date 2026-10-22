"""Signed-out homepage: hypothetical comparison endpoint and invite requests."""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings

from tracker.models import InviteRequest
from tracker.test_study import TODAY, seed


class HomepageTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_signed_out_sees_homepage_without_private_data(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Follow a proven strategy.")
        self.assertContains(response, "Request an invite")
        self.assertContains(response, "Hypothetical, not anyone")
        self.assertContains(response, "still downloading")

    def test_signed_in_owner_skips_homepage(self):
        user = get_user_model().objects.create_user("owner", password="a-long-test-password-1")
        self.client.force_login(user)
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)

    def test_comparison_uses_study_history(self):
        start = TODAY - timedelta(days=800)
        seed("QQQ", start, lambda d, i: 100 * (1.001 ** i))
        seed("SPY", start, lambda d, i: 200.0)
        seed("VTI", start, lambda d, i: 50.0)
        seed("GBPUSD=X", start, lambda d, i: 1.25)
        data = self.client.get("/compare.json?amount=2000&monthly=0&range=1y").json()
        self.assertTrue(data["ready"])
        self.assertEqual([r["key"] for r in data["rows"]], ["nine_sig", "hold", "spy", "vti"])
        spy = next(r for r in data["rows"] if r["key"] == "spy")
        # Flat SPY at a flat rate: value is what went in.
        self.assertAlmostEqual(spy["final"], 2000, places=2)
        self.assertEqual(data["put_in"], 2000)
        self.assertTrue(data["simulated"])
        self.assertLessEqual(len(spy["points"]), 170)
        page = self.client.get("/?range=1y")
        self.assertContains(page, "Quarterly signal")
        self.assertNotContains(page, "9Sig")
        self.assertContains(page, 'id="compare-data"')

    def test_other_strategies_join_the_comparison_under_generic_names(self):
        from tracker.test_study_fixed import seed_all_weather
        start = TODAY - timedelta(days=800)
        seed("QQQ", start, lambda d, i: 100 * (1.001 ** i))
        seed_all_weather(start, start)  # SPY, VTI, GBP/USD and the pound All Weather funds
        data = self.client.get("/compare.json?amount=1000&monthly=0&range=1y").json()
        keys = [r["key"] for r in data["rows"]]
        self.assertIn("all-weather", keys)
        # Strategies whose price history isn't downloaded are left out, not guessed.
        self.assertNotIn("golden-butterfly", keys)
        row = next(r for r in data["rows"] if r["key"] == "all-weather")
        self.assertEqual(row["label"], "All-season mix")
        self.assertEqual(len(row["points"]), len(data["put_in_points"]))
        page = self.client.get("/?range=1y")
        self.assertContains(page, "All-season mix")
        for brand in ("All Weather", "Bridgewater", "Golden Butterfly", "Dual Momentum", "Permanent Portfolio"):
            self.assertNotContains(page, brand)

    def test_comparison_inputs_are_whole_pounds_within_limits(self):
        data = self.client.get("/compare.json?amount=250000&monthly=2500.4&range=bogus").json()
        self.assertEqual((data["amount"], data["monthly"], data["range"]), (250000, 2500, "10y"))
        data = self.client.get("/compare.json?amount=99999999999&monthly=-5").json()
        self.assertEqual((data["amount"], data["monthly"]), (10_000_000, 0))
        data = self.client.get("/compare.json?amount=abc&monthly=37").json()
        self.assertEqual((data["amount"], data["monthly"]), (1000, 37))
        self.assertFalse(data["ready"])


class InviteTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_superuser("boss", "boss@example.com", "a-long-test-password-1")

    @patch("tracker.v2_home.dispatch", return_value={"email": "sent"})
    def test_invite_is_stored_once_and_owner_notified(self, dispatch):
        response = self.client.post("/invite/", {"email": " Friend@Example.com "})
        self.assertRedirects(response, "/?invite=thanks#invite", fetch_redirect_response=False)
        invite = InviteRequest.objects.get()
        self.assertEqual(invite.email, "friend@example.com")
        self.assertTrue(invite.notified)
        dispatch.assert_called_once()
        self.assertEqual(dispatch.call_args.args[0], self.owner)
        self.assertIn("friend@example.com", dispatch.call_args.args[3])
        # Asking again gives the same reply and no second alert.
        self.client.post("/invite/", {"email": "friend@example.com"})
        self.assertEqual(InviteRequest.objects.count(), 1)
        dispatch.assert_called_once()
        self.assertContains(self.client.get("/?invite=thanks"), "on the list")

    @patch("tracker.v2_home.dispatch")
    def test_bots_and_bad_emails_store_nothing(self, dispatch):
        self.assertRedirects(self.client.post("/invite/", {"email": "nope"}),
                             "/?invite=invalid#invite", fetch_redirect_response=False)
        self.assertRedirects(self.client.post("/invite/", {"email": "bot@example.com", "website": "x"}),
                             "/?invite=thanks#invite", fetch_redirect_response=False)
        self.assertEqual(InviteRequest.objects.count(), 0)
        dispatch.assert_not_called()
        self.assertEqual(self.client.get("/invite/").status_code, 405)

    @override_settings(TURNSTILE_REQUIRED=True, TURNSTILE_SITE_KEY="site", TURNSTILE_SECRET_KEY="secret")
    @patch("tracker.v2_home.dispatch")
    def test_turnstile_is_enforced_when_configured(self, dispatch):
        self.client.post("/invite/", {"email": "friend@example.com"})
        self.assertEqual(InviteRequest.objects.count(), 0)
        self.assertContains(self.client.get("/"), "cf-turnstile")

    @patch("tracker.v2_home.dispatch", return_value={"email": "disabled", "discord": "disabled"})
    def test_invite_not_marked_notified_when_no_destination_sent_it(self, dispatch):
        self.client.post("/invite/", {"email": "quiet@example.com"})
        self.assertFalse(InviteRequest.objects.get().notified)

    @patch("tracker.v2_home.DAILY_INVITE_ALERTS", 2)
    @patch("tracker.v2_home.dispatch", return_value={"email": "sent"})
    def test_alerts_are_capped_per_day_but_requests_kept(self, dispatch):
        for n in range(4):
            self.client.post("/invite/", {"email": "p%d@example.com" % n})
        self.assertEqual(InviteRequest.objects.count(), 4)
        self.assertEqual(dispatch.call_count, 2)

    @patch("tracker.v2_home.dispatch", side_effect=RuntimeError("smtp down"))
    def test_alert_failure_keeps_the_request(self, dispatch):
        self.client.post("/invite/", {"email": "friend@example.com"})
        self.assertTrue(InviteRequest.objects.filter(email="friend@example.com").exists())

    def test_csrf_is_required(self):
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.post("/invite/", {"email": "friend@example.com"}).status_code, 403)


class FooterAndPrivacyTests(TestCase):
    def test_footer_on_public_pages_and_privacy_notice(self):
        for path in ("/", "/guides/"):
            page = self.client.get(path)
            for text in ("Works with", "Fidelity", "Trade Republic", "The small print",
                         "not affiliated", "No broker connection", 'href="/privacy/"'):
                self.assertContains(page, text, msg_prefix=path)
        home = self.client.get("/")
        self.assertNotContains(home, "Leveraged ISA rebalancing")
        self.assertNotContains(home, "A quarterly growth-target plan for a UK ISA is available now")
        privacy = self.client.get("/privacy/")
        self.assertEqual(privacy.status_code, 200)
        self.assertContains(privacy, "your email address when you ask for an invite")
        self.assertContains(privacy, "Reply to any email")
        self.assertNotIn("noindex", privacy.get("X-Robots-Tag", ""))
        self.assertContains(self.client.get("/sitemap.xml"), "/privacy/")
        with override_settings(CONTACT_EMAIL="owner@example.invalid"):
            self.assertContains(self.client.get("/privacy/"), "mailto:owner@example.invalid")
