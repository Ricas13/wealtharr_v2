"""The SaaS pages in a real browser, phone and desktop: sign up and confirm, buy a plan, set up payments."""
import re
from unittest import mock

from django.test import override_settings
from django.utils import timezone

from browser_tests.harness import BrowserHarness
from tenancy import gateways
from tenancy.models import Plan
from tracker.models import PlanProviderMapping, SystemIntegration


def mail_on():
    cfg = SystemIntegration.current()
    cfg.smtp_host, cfg.smtp_sender = "smtp.invalid", "noreply@example.test"
    cfg.save()


@override_settings(SELF_SERVE_SIGNUP=True)
class SignupBrowserTests(BrowserHarness):
    def test_sign_up_confirm_email_and_land_in_the_app(self):
        mail_on()
        sent = []
        with mock.patch("tenancy.accounts.send_system_email", lambda to, subject, body: sent.append(body) or True):
            for width in (360, 1280):
                with self.subTest(width=width):
                    sent.clear()
                    name = "newcomer%d" % width
                    self.new_page(width)
                    self.page.goto(self.live_server_url + "/", wait_until="networkidle")
                    self.page.get_by_role("link", name="Create account").first.click()
                    self.page.wait_for_load_state("networkidle")
                    self.page.locator("#id_username").fill(name)
                    self.page.locator("#id_email").fill(name + "@example.test")
                    self.page.locator("#id_password1").fill("Sturdy-Pass-91xq")
                    self.page.locator("#id_password2").fill("Sturdy-Pass-91xq")
                    self.assertTrue(self.page.get_by_role("link", name="Terms").first.is_visible())
                    self.page.locator("#id_accept_terms").check()
                    self.page.get_by_role("button", name="Create account").click()
                    self.assertTrue(self.page.get_by_text("Check your email").first.is_visible())
                    link = re.search(r"/signup/verify/\S+/", sent[-1]).group(0)
                    self.page.goto(self.live_server_url + link, wait_until="networkidle")
                    self.page.get_by_role("button", name="Activate my account").click()
                    self.page.wait_for_load_state("networkidle")
                    self.page.locator("#id_username").fill(name)
                    self.page.locator("#id_password").fill("Sturdy-Pass-91xq")
                    self.page.get_by_role("button", name="Sign in", exact=False).click()
                    self.page.wait_for_load_state("networkidle")
                    self.assertIn("/overview/", self.page.url)
                    self.assert_reachable_mobile()


class PlansAndPaymentsBrowserTests(BrowserHarness):
    def setUp(self):
        super().setUp()
        gateways.save("stripe", {"enabled": True, "restricted_key": "rk_test_b1", "webhook_secret": "whsec_browser"})
        self.plan = Plan.objects.create(slug="plus", name="Plus", is_public=True, price_label="£4 / month",
                                        description="More strategies")
        PlanProviderMapping.objects.create(plan=self.plan, provider="stripe", external_id="price_b1",
                                           verified_at=timezone.now())

    def test_plans_page_pays_through_the_gateway_at_both_widths(self):
        for width in (360, 1280):
            with self.subTest(width=width):
                self.new_page(width)
                self.sign_in()
                self.visit("plans")
                self.assertTrue(self.page.get_by_text("£4 / month").is_visible())
                self.assert_reachable_mobile()
                # The server's answer is a redirect to Stripe's page; stop the browser short of the real network.
                self.ctx.route("https://checkout.stripe.com/**", lambda route: route.abort())
                with mock.patch("tenancy.gateways.call", return_value=(200, {"url": "https://checkout.stripe.com/pay/x"})):
                    with self.page.expect_request(re.compile(r"checkout\.stripe\.com")) as sent:
                        self.page.get_by_role("button", name="Pay with Stripe").click()
                self.assertEqual(sent.value.url, "https://checkout.stripe.com/pay/x")

    def test_platform_admin_sets_up_a_gateway_and_tests_it(self):
        self.owner.is_staff = self.owner.is_superuser = True
        self.owner.save()
        for width in (360, 1280):
            with self.subTest(width=width):
                self.new_page(width)
                self.sign_in()
                self.visit("control")
                card = self.page.locator("#paypal")
                self.assertTrue(card.get_by_text("Not set up").is_visible())
                card.get_by_text("Set up PayPal").click()
                card.locator("input[name='paypal-client_id']").fill("client-id-b1")
                card.locator("input[name='paypal-client_secret']").fill("client-secret-b1")
                card.locator("input[name='paypal-webhook_id']").fill("WH-B1")
                card.locator("input[name='paypal-enabled']").check()
                card.get_by_role("button", name="Save", exact=True).click()
                self.page.wait_for_load_state("networkidle")
                self.assertTrue(self.page.locator("#paypal").get_by_text("Ready for checkout").is_visible())
                self.assertEqual(self.page.locator("#paypal input[name='paypal-client_secret']").input_value(), "")
                with mock.patch("tenancy.gateways.call", return_value=(200, {"access_token": "t"})):
                    self.page.locator("#paypal").get_by_role("button", name="Test connection").click()
                    self.page.wait_for_load_state("networkidle")
                self.assertTrue(self.page.get_by_text("connection works").first.is_visible())
                self.assert_reachable_mobile()
                gateways.use_environment("paypal")
