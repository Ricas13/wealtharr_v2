import hashlib
import hmac
import json
import time
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from tracker.models import PlanProviderMapping, ProviderSubscription

from . import billing, gateways
from .models import Plan, UserAccess

User = get_user_model()
CALL = "tenancy.gateways.call"
MAIL = "tenancy.billing.send_system_email"


def stripe_post(client, event, secret="whsec_life"):
    body = json.dumps(event).encode()
    stamp = int(time.time())
    sig = hmac.new(secret.encode(), f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
    return client.post("/webhooks/stripe/", body, content_type="application/json", HTTP_STRIPE_SIGNATURE=f"t={stamp},v1={sig}")


class Base(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("pat", "pat@x.test", "pw-User-1234")
        self.default = Plan.objects.get(is_default=True)
        self.pro = Plan.objects.create(slug="pro", name="Pro", is_public=True)
        gateways.save("stripe", {"enabled": True, "restricted_key": "rk_test_l1", "webhook_secret": "whsec_life"})
        gateways.save("paypal", {"enabled": True, "environment": "sandbox", "client_id": "cid",
                                 "client_secret": "cs", "webhook_id": "WH-1"})
        for provider, ext in (("stripe", "price_1"), ("paypal", "P-AB123")):
            PlanProviderMapping.objects.create(plan=self.pro, provider=provider, external_id=ext, verified_at=timezone.now())

    def paypal(self, kind, sub_id="I-1", **resource):
        event = {"id": f"WH-{kind}-{sub_id}-{time.time_ns()}", "event_type": kind, "resource": {"id": sub_id, **resource}}
        with mock.patch("tenancy.gateways.verify_paypal_webhook", return_value=True):
            return self.client.post("/webhooks/paypal/", json.dumps(event), content_type="application/json")

    def plan_of(self):
        return UserAccess.objects.get(user=self.user).plan


class StripeLifecycle(Base):
    def bought(self, event_id="evt_a", sub="sub_1"):
        stripe_post(self.client, {"id": event_id, "type": "checkout.session.completed", "data": {"object": {
            "payment_status": "paid", "client_reference_id": str(self.user.pk), "customer": "cus_1",
            "subscription": sub, "metadata": {"plan_slug": "pro"}}}})

    def test_purchase_records_the_subscription_and_emails_the_person(self):
        with mock.patch(MAIL) as mail:
            self.bought()
        self.assertTrue(ProviderSubscription.objects.filter(provider="stripe", external_id="sub_1", user=self.user).exists())
        self.assertEqual(mail.call_args.args[0], "pat@x.test")

    def test_ending_that_subscription_returns_to_default_and_marks_it_over(self):
        self.bought()
        stripe_post(self.client, {"id": "evt_b", "type": "customer.subscription.deleted",
                                  "data": {"object": {"id": "sub_1", "customer": "cus_1"}}})
        self.assertEqual(self.plan_of(), self.default)
        self.assertIsNotNone(ProviderSubscription.objects.get(external_id="sub_1").ended_at)

    def test_another_running_subscription_keeps_the_plan(self):
        self.bought()
        ProviderSubscription.objects.create(provider="paypal", external_id="I-9", user=self.user)
        stripe_post(self.client, {"id": "evt_c", "type": "customer.subscription.deleted",
                                  "data": {"object": {"id": "sub_1", "customer": "cus_1"}}})
        self.assertEqual(self.plan_of(), self.pro)

    def test_failed_payment_emails_but_changes_nothing(self):
        self.bought()
        with mock.patch(MAIL) as mail:
            stripe_post(self.client, {"id": "evt_d", "type": "invoice.payment_failed", "data": {"object": {"customer": "cus_1"}}})
        self.assertIn("payment", mail.call_args.args[1].lower())
        self.assertEqual(self.plan_of(), self.pro)


class PayPalLifecycle(Base):
    def activate(self):
        self.paypal("BILLING.SUBSCRIPTION.ACTIVATED", plan_id="P-AB123", custom_id=str(self.user.pk))

    def test_cancelling_with_time_left_keeps_the_plan_until_the_paid_period_ends(self):
        self.activate()
        end = (timezone.now() + timedelta(days=10)).isoformat()
        self.paypal("BILLING.SUBSCRIPTION.CANCELLED", billing_info={"next_billing_time": end})
        self.assertEqual(self.plan_of(), self.pro)
        sub = ProviderSubscription.objects.get(external_id="I-1")
        self.assertIsNotNone(sub.ends_at)
        self.assertIsNone(sub.ended_at)
        self.assertEqual(billing.expire_due_subscriptions(), 0)
        self.assertEqual(billing.expire_due_subscriptions(now=timezone.now() + timedelta(days=11)), 1)
        self.assertEqual(self.plan_of(), self.default)

    def test_cancelling_with_no_time_left_ends_now_and_so_do_expiry_and_suspension(self):
        for kind in ("BILLING.SUBSCRIPTION.CANCELLED", "BILLING.SUBSCRIPTION.EXPIRED", "BILLING.SUBSCRIPTION.SUSPENDED"):
            UserAccess.objects.filter(user=self.user).delete()
            ProviderSubscription.objects.all().delete()
            self.activate()
            self.assertEqual(self.plan_of(), self.pro)
            self.paypal(kind)
            self.assertEqual(self.plan_of(), self.default, kind)

    def test_failed_payment_emails(self):
        self.activate()
        with mock.patch(MAIL) as mail:
            self.paypal("BILLING.SUBSCRIPTION.PAYMENT.FAILED")
        self.assertIn("payment", mail.call_args.args[1].lower())

    def test_the_hourly_job_runs_the_expiry(self):
        with mock.patch("tenancy.billing.expire_due_subscriptions") as expire, \
                mock.patch("tracker.management.commands.sync_market_and_signals.sync_ticker"):
            call_command("sync_market_and_signals", verbosity=0)
        expire.assert_called_once()


class ManagingAndClosing(Base):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        UserAccess.objects.update_or_create(user=self.user, defaults={"plan": self.pro, "stripe_customer_id": "cus_1"})

    def test_manage_billing_goes_to_the_stripe_portal(self):
        with mock.patch(CALL, return_value=(200, {"url": "https://billing.stripe.com/p/x"})) as call:
            response = self.client.post("/plans/manage/stripe/")
        self.assertEqual(response.url, "https://billing.stripe.com/p/x")
        self.assertEqual(call.call_args.kwargs["form"]["customer"], "cus_1")
        self.assertContains(self.client.get("/plans/"), "Manage billing")

    def test_a_second_checkout_is_refused_so_nobody_is_billed_twice(self):
        with mock.patch(CALL) as call:
            response = self.client.post("/plans/pro/buy/stripe/", follow=True)
            call.assert_not_called()
        self.assertContains(response, "charged twice")

    def test_cancel_paypal_calls_paypal_and_says_when_it_ends(self):
        ProviderSubscription.objects.create(provider="paypal", external_id="I-5", user=self.user)
        with mock.patch(CALL, side_effect=[(200, {"access_token": "t"}), (204, {})]) as call:
            response = self.client.post("/plans/manage/paypal/cancel/", follow=True)
        self.assertIn("/v1/billing/subscriptions/I-5/cancel", call.call_args_list[-1].args[1])
        self.assertContains(response, "stays until the period you paid for ends")

    def test_closing_the_account_cancels_subscriptions_first(self):
        ProviderSubscription.objects.create(provider="paypal", external_id="I-5", user=self.user)
        replies = [(200, {"data": [{"id": "sub_1"}]}), (200, {}), (200, {"access_token": "t"}), (204, {})]
        with mock.patch(CALL, side_effect=replies) as call:
            self.client.post("/account/delete/", {"confirm": "DELETE", "password": "pw-User-1234"})
        urls = [c.args[1] for c in call.call_args_list]
        self.assertTrue(any("/subscriptions/sub_1" in u for u in urls))
        self.assertTrue(any("I-5/cancel" in u for u in urls))
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertFalse(ProviderSubscription.objects.filter(ended_at__isnull=True).exists())

    def test_if_a_gateway_will_not_cancel_the_person_is_told_to_do_it_themselves(self):
        with mock.patch(CALL, return_value=(500, {})):
            response = self.client.post("/account/delete/", {"confirm": "DELETE", "password": "pw-User-1234"}, follow=True)
        self.assertContains(response, "could not cancel your subscription with Stripe")
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
