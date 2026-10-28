import hashlib
import hmac
import json
import time

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from . import billing
from .models import AuditEvent, BillingEvent, Plan, UserAccess

SECRET = "whsec_test"


def signed(body, secret=SECRET, stamp=None):
    stamp = int(stamp or time.time())
    mac = hmac.new(secret.encode(), f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={stamp},v1={mac}"


@override_settings(STRIPE_WEBHOOK_SECRET=SECRET)
class StripeWebhookTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("pat", password="x")
        self.full = Plan.objects.get(is_default=True)
        self.pro = Plan.objects.create(slug="pro", name="Pro", stripe_payment_link_id="plink_1", is_public=True)

    def post(self, event, header=None, body=None):
        body = body or json.dumps(event).encode()
        return self.client.post("/webhooks/stripe/", body, content_type="application/json",
                                HTTP_STRIPE_SIGNATURE=header if header is not None else signed(body))

    def checkout(self, event_id="evt_1", **over):
        obj = {"payment_status": "paid", "client_reference_id": str(self.user.pk),
               "payment_link": "plink_1", "customer": "cus_9"}
        obj.update(over)
        return {"id": event_id, "type": "checkout.session.completed", "data": {"object": obj}}

    def test_paid_checkout_assigns_plan_and_audits(self):
        self.assertEqual(self.post(self.checkout()).status_code, 200)
        access = UserAccess.objects.get(user=self.user)
        self.assertEqual((access.plan, access.stripe_customer_id), (self.pro, "cus_9"))
        self.assertTrue(AuditEvent.objects.filter(action="billing.plan_assigned", subject_id=self.user.pk).exists())

    def test_redelivery_is_harmless(self):
        self.post(self.checkout())
        UserAccess.objects.filter(user=self.user).update(plan=self.full)
        self.assertEqual(self.post(self.checkout()).content, b"duplicate")
        self.assertEqual(UserAccess.objects.get(user=self.user).plan, self.full)
        self.assertEqual(BillingEvent.objects.count(), 1)

    def test_bad_or_old_signature_rejected(self):
        body = json.dumps(self.checkout()).encode()
        self.assertEqual(self.post(None, header="t=1,v1=00", body=body).status_code, 400)
        self.assertEqual(self.post(None, header=signed(body, stamp=time.time() - 3600), body=body).status_code, 400)
        self.assertEqual(self.post(None, header=signed(body, secret="other"), body=body).status_code, 400)
        self.assertFalse(UserAccess.objects.exists())

    def test_unpaid_unknown_link_or_user_changes_nothing(self):
        for i, over in enumerate(({"payment_status": "unpaid"}, {"payment_link": "plink_x"},
                                  {"client_reference_id": "99999"}, {"client_reference_id": "abc"})):
            self.assertEqual(self.post(self.checkout(f"evt_{i}", **over)).status_code, 200)
        self.assertFalse(UserAccess.objects.exists())

    def test_subscription_deleted_reverts_to_default(self):
        self.post(self.checkout())
        event = {"id": "evt_2", "type": "customer.subscription.deleted", "data": {"object": {"customer": "cus_9"}}}
        self.post(event)
        self.assertEqual(UserAccess.objects.get(user=self.user).plan, self.full)

    @override_settings(STRIPE_WEBHOOK_SECRET="")
    def test_off_without_secret(self):
        self.assertEqual(self.post(self.checkout()).status_code, 404)

    def test_get_not_allowed(self):
        self.assertEqual(self.client.get("/webhooks/stripe/").status_code, 405)


class PlansPageTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("sam", password="x")
        self.client.force_login(self.user)
        Plan.objects.create(slug="pro", name="Pro", is_public=True, price_label="£5 / month",
                            stripe_link="https://buy.stripe.com/abc", stripe_payment_link_id="plink_1",
                            paypal_link="https://paypal.com/x")
        Plan.objects.create(slug="hidden", name="Hidden", is_public=False)

    def test_shows_current_and_public_plans_with_reference(self):
        html = self.client.get("/plans/").content.decode()
        self.assertIn("Full", html)
        self.assertIn(f"https://buy.stripe.com/abc?client_reference_id={self.user.pk}", html)
        self.assertIn("https://paypal.com/x", html)
        self.assertNotIn("Hidden", html)

    def test_requires_login(self):
        self.client.logout()
        self.assertEqual(self.client.get("/plans/").status_code, 302)
