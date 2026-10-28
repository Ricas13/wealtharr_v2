"""One whole-service journey through the real views: sign up, verify, sign in, add a strategy,
pay (gateway mocked), hit a plan limit, be isolated from another user, get an alert, export, close."""
import hashlib
import hmac
import json
import re
import time
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from tenancy import gateways
from tenancy.models import Plan, UserAccess
from tracker.models import NotificationDelivery, NotificationPreference, PlanProviderMapping, StrategyInstance, SystemIntegration
from tracker.services import dispatch

User = get_user_model()
GOOD_LOGIN = "Sturdy-Pass-91xq"


@override_settings(SELF_SERVE_SIGNUP=True)
class WholeJourney(TestCase):
    def setUp(self):
        cfg = SystemIntegration.current()
        cfg.smtp_host, cfg.smtp_sender = "smtp.invalid", "noreply@example.test"
        cfg.save()
        self.mail = []
        patcher = mock.patch("tenancy.accounts.send_system_email",
                             lambda to, subject, body: self.mail.append((to, body)) or True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def sign_up(self, client, name):
        client.post("/signup/", {"username": name, "email": f"{name}@example.test",
                                 "password1": GOOD_LOGIN, "password2": GOOD_LOGIN,
                                 "accept_terms": "on"})
        link = re.search(r"/signup/verify/\S+/", self.mail[-1][1]).group(0)
        client.post(link)
        self.assertTrue(client.login(username=name, password=GOOD_LOGIN))

    def add_strategy(self, client, name):
        start = (timezone.localdate() - timedelta(days=30)).isoformat()
        return client.post("/add-strategy/", {
            "strategy": "all-weather", "platform": "TRADING212", "account_type": "ISA", "ack": "yes",
            "mode": "fresh", "name": name, "start": start, "amount": "1000", "deposited": "yes"}, follow=True)

    def test_the_journey(self):
        ann, ben = self.client_class(), self.client_class()
        self.sign_up(ann, "ann")
        self.sign_up(ben, "ben")

        # A new person lands somewhere sensible and can add a strategy.
        self.assertEqual(ann.get("/overview/").status_code, 200)
        page = self.add_strategy(ann, "Ann AW")
        self.assertEqual(StrategyInstance.objects.filter(owner__username="ann").count(), 1, page.content[:2000])
        strategy = StrategyInstance.objects.get(owner__username="ann")

        # Isolation: Ben sees none of it.
        self.assertEqual(ben.get(f"/s/{strategy.pk}/today/").status_code, 404)
        self.assertNotContains(ben.get("/overview/"), "Ann AW")

        # A paid plan with a one-strategy cap, sold through a mocked Stripe.
        plan = Plan.objects.create(slug="starter", name="Starter", is_public=True, price_label="£3 / month",
                                   features=[], limits={"strategies.max": 1})
        gateways.save("stripe", {"enabled": True, "restricted_key": "rk_test_x1", "webhook_secret": "whsec_journey"})
        PlanProviderMapping.objects.create(plan=plan, provider="stripe", external_id="price_1", verified_at=timezone.now())
        self.assertContains(ann.get("/plans/"), "Pay with Stripe")
        with mock.patch("tenancy.gateways.call", return_value=(200, {"url": "https://checkout.stripe.com/x"})):
            self.assertEqual(ann.post("/plans/starter/buy/stripe/").url, "https://checkout.stripe.com/x")
        event = {"id": "evt_j1", "type": "checkout.session.completed", "data": {"object": {
            "payment_status": "paid", "client_reference_id": str(strategy.owner_id), "customer": "cus_j",
            "metadata": {"plan_slug": "starter"}}}}
        body = json.dumps(event).encode()
        stamp = int(time.time())
        sig = hmac.new(b"whsec_journey", f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
        self.assertEqual(self.client.post("/webhooks/stripe/", body, content_type="application/json",
                                          HTTP_STRIPE_SIGNATURE=f"t={stamp},v1={sig}").status_code, 200)
        self.assertEqual(UserAccess.objects.get(user__username="ann").plan, plan)

        # The plan's cap now applies to her second strategy, with a plain message.
        again = self.add_strategy(ann, "Ann second")
        self.assertEqual(StrategyInstance.objects.filter(owner__username="ann").count(), 1)
        self.assertContains(again, "allows 1 strateg")

        # An alert reaches her chosen email through the site's mail server.
        NotificationPreference.objects.update_or_create(
            owner=User.objects.get(username="ann"), defaults={"destination_email": "ann@example.test", "email_enabled": True})
        with mock.patch("tracker.services._send_message") as send:
            dispatch(User.objects.get(username="ann"), "journey-1", "Hello", "Body", channels=["email"])
        self.assertTrue(send.called)
        self.assertEqual(NotificationDelivery.objects.filter(owner__username="ann", status="SENT").count(), 1)

        # The hourly job copes with several people's data.
        with mock.patch("tracker.management.commands.sync_market_and_signals.sync_ticker"):
            call_command("sync_market_and_signals", verbosity=0)

        # She can take her data out and close her account; the subscription ending is handled too.
        self.assertEqual(ann.post("/account/export/").status_code, 200)
        cancel = {"id": "evt_j2", "type": "customer.subscription.deleted", "data": {"object": {"customer": "cus_j"}}}
        body = json.dumps(cancel).encode()
        sig = hmac.new(b"whsec_journey", f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
        self.client.post("/webhooks/stripe/", body, content_type="application/json", HTTP_STRIPE_SIGNATURE=f"t={stamp},v1={sig}")
        self.assertTrue(UserAccess.objects.get(user__username="ann").plan.is_default)
        ann.post("/account/delete/", {"confirm": "DELETE", "password": GOOD_LOGIN})
        self.assertFalse(self.client_class().login(username="deleted-%d" % strategy.owner_id, password=GOOD_LOGIN))
        self.assertEqual(ben.get("/overview/").status_code, 200)


@override_settings(SELF_SERVE_SIGNUP=True)
class SignupEdges(TestCase):
    def mail_on(self):
        cfg = SystemIntegration.current()
        cfg.smtp_host, cfg.smtp_sender = "smtp.invalid", "noreply@example.test"
        cfg.save()

    def test_signup_is_closed_while_mail_is_not_set_up(self):
        self.assertEqual(self.client.get("/signup/").status_code, 404)
        self.assertNotContains(self.client.get("/"), "/signup/")
        self.assertNotContains(self.client.get("/login/"), "/signup/")

    def test_when_open_the_homepage_and_login_offer_create_account(self):
        self.mail_on()
        self.assertContains(self.client.get("/"), 'href="/signup/"')
        self.assertContains(self.client.get("/login/"), "/signup/")

    def test_terms_must_be_accepted_and_the_acceptance_is_audited(self):
        from tenancy.models import AuditEvent
        self.mail_on()
        sent = []
        with mock.patch("tenancy.accounts.send_system_email", lambda to, subject, body: sent.append(to) or True):
            self.client.post("/signup/", {"username": "kay", "email": "k@example.test",
                                          "password1": GOOD_LOGIN, "password2": GOOD_LOGIN})
            self.assertFalse(User.objects.filter(username="kay").exists())
            self.client.post("/signup/", {"username": "kay", "email": "k@example.test",
                                          "password1": GOOD_LOGIN, "password2": GOOD_LOGIN, "accept_terms": "on"})
        self.assertTrue(User.objects.filter(username="kay").exists())
        self.assertTrue(AuditEvent.objects.filter(action="account.signup", metadata__accepted_terms=True).exists())

    def test_unconfirmed_person_is_told_why_and_can_ask_for_the_link_again(self):
        self.mail_on()
        sent = []
        with mock.patch("tenancy.accounts.send_system_email", lambda to, subject, body: sent.append((to, body)) or True):
            self.client.post("/signup/", {"username": "lou", "email": "l@example.test", "password1": GOOD_LOGIN,
                                          "password2": GOOD_LOGIN, "accept_terms": "on"})
            wrong = self.client.post("/login/", {"username": "lou", "password": "wrong-password-1"})
            self.assertNotContains(wrong, "confirm your email")  # a guesser learns nothing
            right = self.client.post("/login/", {"username": "lou", "password": GOOD_LOGIN})
            self.assertContains(right, "confirm your email")
            self.assertContains(right, "Send it again")
            self.client.post("/signup/resend/", {"email": "l@example.test"})
            self.client.post("/signup/resend/", {"email": "nobody@example.test"})
        self.assertEqual(len(sent), 2)  # the original and one resend; none for the unknown address

    def test_closed_accounts_are_left_out_of_the_hourly_job(self):
        from datetime import timedelta
        from tracker.test_tenancy_core import _strategy
        owner = User.objects.create_user("gone", password=GOOD_LOGIN)
        strategy = _strategy(owner, "Gone")
        StrategyInstance.objects.filter(pk=strategy.pk).update(market_data_enabled=True, tracking_only=False)
        User.objects.filter(pk=owner.pk).update(is_active=False)
        with mock.patch("tracker.management.commands.sync_market_and_signals.module_for") as module:
            call_command("sync_market_and_signals", verbosity=0)
        module.assert_not_called()
