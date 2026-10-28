import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from tracker.models import PlanProviderMapping, ProviderSubscription

from . import billing, gateways
from .gateway_http import GatewayError
from .models import AuditEvent, BillingEvent, Plan, UserAccess

User = get_user_model()
CALL = "tenancy.gateways.call"


def save_stripe(**over):
    data = {"enabled": True, "restricted_key": "rk_test_abc", "webhook_secret": "whsec_abc"}
    data.update(over)
    gateways.save("stripe", data)


def save_paypal(**over):
    data = {"enabled": True, "environment": "sandbox", "client_id": "cid", "client_secret": "csecret",
            "webhook_id": "WH-1"}
    data.update(over)
    gateways.save("paypal", data)


class SettingsTests(TestCase):
    def test_secrets_are_encrypted_kept_replaced_cleared_and_never_audited(self):
        from tracker.models import PaymentProviderCredential
        save_stripe()
        blob = PaymentProviderCredential.objects.get(provider="stripe").secrets_encrypted
        self.assertNotIn("rk_test_abc", blob)
        self.assertEqual(gateways.effective("stripe").restricted_key, "rk_test_abc")
        save_stripe(restricted_key="", webhook_secret="")  # blank keeps
        self.assertEqual(gateways.effective("stripe").webhook_secret, "whsec_abc")
        save_stripe(restricted_key="rk_test_new", webhook_secret="", clear_webhook_secret=True)
        self.assertEqual(gateways.effective("stripe").restricted_key, "rk_test_new")
        self.assertEqual(gateways.effective("stripe").webhook_secret, "")
        events = json.dumps(list(AuditEvent.objects.filter(action__startswith="payments.").values("metadata")))
        self.assertNotIn("rk_test", events)
        self.assertNotIn("whsec", events)

    def test_switched_off_keeps_credentials_but_offers_none(self):
        save_stripe(enabled=False)
        self.assertFalse(gateways.status("stripe")["checkout_ready"])
        self.assertEqual(gateways.effective("stripe").restricted_key, "")
        self.assertTrue(gateways.status("stripe")["credentials"])
        save_stripe(enabled=True)
        self.assertTrue(gateways.status("stripe")["checkout_ready"])

    @override_settings(STRIPE_WEBHOOK_SECRET="whsec_env")
    def test_environment_is_the_fallback_and_use_environment_returns_to_it(self):
        self.assertEqual(gateways.effective("stripe").webhook_secret, "whsec_env")
        save_stripe()
        self.assertEqual(gateways.status("stripe")["source"], "database")
        gateways.use_environment("stripe")
        self.assertEqual(gateways.status("stripe")["source"], "environment")

    def test_checkout_needs_both_credentials_and_webhook(self):
        save_paypal(webhook_id="")
        self.assertFalse(gateways.checkout_ready("paypal"))


class ConnectionAndMappingTests(TestCase):
    def setUp(self):
        self.plan = Plan.objects.create(slug="pro", name="Pro", is_public=True)

    def test_connection_messages_are_honest(self):
        save_stripe()
        with mock.patch(CALL, return_value=(401, {"error": {"message": "Invalid API Key"}})):
            self.assertEqual(gateways.test_connection("stripe"), (False, "Invalid API Key"))
        with mock.patch(CALL, return_value=(200, {"data": []})):
            ok, message = gateways.test_connection("stripe")
        self.assertTrue(ok)
        self.assertIn("does not prove", message)
        save_paypal()
        with mock.patch(CALL, return_value=(200, {"access_token": "t"})):
            self.assertTrue(gateways.test_connection("paypal")[0])
        with mock.patch(CALL, side_effect=GatewayError("Connection timed out after 10 seconds.")):
            self.assertFalse(gateways.test_connection("paypal")[0])

    def test_stripe_price_must_be_active_and_recurring(self):
        save_stripe()
        m = PlanProviderMapping.objects.create(plan=self.plan, provider="stripe", external_id="price_123")
        for body, ok in (({"active": True, "recurring": {"interval": "month"}}, True),
                         ({"active": True, "recurring": None}, False),
                         ({"active": False, "recurring": {"interval": "month"}}, False)):
            with mock.patch(CALL, return_value=(200, body)):
                self.assertEqual(gateways.verify_mapping(m), ok)
            m.refresh_from_db()
            self.assertEqual(m.verified_at is not None, ok)

    def test_paypal_plan_must_be_active_and_ids_are_validated_before_use(self):
        save_paypal()
        m = PlanProviderMapping.objects.create(plan=self.plan, provider="paypal", external_id="P-ABC123")
        with mock.patch(CALL, side_effect=[(200, {"access_token": "t"}), (200, {"status": "ACTIVE"})]):
            self.assertTrue(gateways.verify_mapping(m))
        bad = PlanProviderMapping.objects.create(plan=Plan.objects.create(slug="x", name="X"), provider="stripe",
                                                 external_id="price_1/../../account")
        with mock.patch(CALL) as call:
            self.assertFalse(gateways.verify_mapping(bad))
            call.assert_not_called()

    def test_unverified_mapping_is_not_sellable(self):
        save_stripe()
        PlanProviderMapping.objects.create(plan=self.plan, provider="stripe", external_id="price_123")
        self.assertEqual(gateways.sellable_providers(self.plan), [])


class CheckoutTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("pat", "pat@x.test", "pw-User-1234")
        self.plan = Plan.objects.create(slug="pro", name="Pro", is_public=True)
        save_stripe()
        save_paypal()
        for provider, ext in (("stripe", "price_123"), ("paypal", "P-ABC123")):
            PlanProviderMapping.objects.create(plan=self.plan, provider=provider, external_id=ext,
                                               verified_at="2026-10-28T00:00:00Z")
        self.client.force_login(self.user)

    def test_stripe_session_names_the_user_plan_and_price(self):
        with mock.patch(CALL, return_value=(200, {"url": "https://checkout.stripe.com/c/pay/x"})) as call:
            response = self.client.post("/plans/pro/buy/stripe/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "https://checkout.stripe.com/c/pay/x")
        form = call.call_args.kwargs["form"]
        self.assertEqual((form["client_reference_id"], form["metadata[plan_slug]"], form["line_items[0][price]"],
                          form["mode"]), (str(self.user.pk), "pro", "price_123", "subscription"))
        self.assertTrue(AuditEvent.objects.filter(action="billing.checkout_started").exists())

    def test_paypal_subscription_carries_the_user_and_returns_the_approve_link(self):
        reply = {"links": [{"rel": "self", "href": "https://x"}, {"rel": "approve", "href": "https://www.paypal.com/approve"}]}
        with mock.patch(CALL, side_effect=[(200, {"access_token": "t"}), (201, reply)]) as call:
            response = self.client.post("/plans/pro/buy/paypal/")
        self.assertEqual(response.url, "https://www.paypal.com/approve")
        self.assertEqual(call.call_args.kwargs["json_body"]["custom_id"], str(self.user.pk))

    def test_gateway_failure_shows_a_message_and_goes_back(self):
        with mock.patch(CALL, return_value=(400, {"error": {"message": "No such price"}})):
            response = self.client.post("/plans/pro/buy/stripe/", follow=True)
        self.assertContains(response, "Could not start checkout: No such price")

    def test_unverified_unknown_or_private_plans_cannot_be_bought(self):
        PlanProviderMapping.objects.update(verified_at=None)
        with mock.patch(CALL) as call:
            self.client.post("/plans/pro/buy/stripe/")
            call.assert_not_called()
        self.assertEqual(self.client.post("/plans/nope/buy/stripe/").status_code, 404)
        Plan.objects.filter(pk=self.plan.pk).update(is_public=False)
        self.assertEqual(self.client.post("/plans/pro/buy/stripe/").status_code, 404)

    def test_plans_page_offers_buttons_only_when_sellable(self):
        self.assertContains(self.client.get("/plans/"), "Pay with Stripe")
        PlanProviderMapping.objects.update(verified_at=None)
        self.assertNotContains(self.client.get("/plans/"), "Pay with Stripe")

    def test_get_is_refused(self):
        self.assertEqual(self.client.get("/plans/pro/buy/stripe/").status_code, 405)


class WebhookTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("pat", "pat@x.test", "pw-User-1234")
        self.full = Plan.objects.get(is_default=True)
        self.pro = Plan.objects.create(slug="pro", name="Pro", is_public=True)
        PlanProviderMapping.objects.create(plan=self.pro, provider="paypal", external_id="P-ABC123",
                                           verified_at="2026-10-28T00:00:00Z")
        PlanProviderMapping.objects.create(plan=self.pro, provider="stripe", external_id="price_123",
                                           verified_at="2026-10-28T00:00:00Z")

    def paypal(self, event, verified=True):
        save_paypal()
        with mock.patch("tenancy.gateways.verify_paypal_webhook", return_value=verified):
            return self.client.post("/webhooks/paypal/", json.dumps(event), content_type="application/json")

    def activated(self, event_id="WH-EV-1"):
        return {"id": event_id, "event_type": "BILLING.SUBSCRIPTION.ACTIVATED",
                "resource": {"id": "I-SUB1", "plan_id": "P-ABC123", "custom_id": str(self.user.pk)}}

    def test_paypal_activation_assigns_and_cancellation_reverts(self):
        self.assertEqual(self.paypal(self.activated()).status_code, 200)
        self.assertEqual(UserAccess.objects.get(user=self.user).plan, self.pro)
        self.assertTrue(ProviderSubscription.objects.filter(external_id="I-SUB1", user=self.user).exists())
        self.paypal({"id": "WH-EV-2", "event_type": "BILLING.SUBSCRIPTION.CANCELLED", "resource": {"id": "I-SUB1"}})
        self.assertEqual(UserAccess.objects.get(user=self.user).plan, self.full)

    def test_unverified_paypal_delivery_changes_nothing(self):
        self.assertEqual(self.paypal(self.activated(), verified=False).status_code, 400)
        self.assertFalse(UserAccess.objects.exists())

    def test_paypal_redelivery_is_harmless_and_endpoint_is_off_without_setup(self):
        self.paypal(self.activated())
        self.assertEqual(self.paypal(self.activated()).content, b"duplicate")
        self.assertEqual(BillingEvent.objects.filter(provider="paypal").count(), 1)
        gateways.use_environment("paypal")
        self.assertEqual(self.client.post("/webhooks/paypal/", "{}", content_type="application/json").status_code, 404)

    def test_paypal_verification_asks_paypal_itself(self):
        save_paypal()
        headers = {"Paypal-Auth-Algo": "a", "Paypal-Cert-Url": "https://api.paypal.com/c", "Paypal-Transmission-Id": "i",
                   "Paypal-Transmission-Sig": "s", "Paypal-Transmission-Time": "t"}
        with mock.patch(CALL, side_effect=[(200, {"access_token": "t"}), (200, {"verification_status": "SUCCESS"})]):
            self.assertTrue(gateways.verify_paypal_webhook(headers, "{}"))
        with mock.patch(CALL, side_effect=[(200, {"access_token": "t"}), (200, {"verification_status": "FAILURE"})]):
            self.assertFalse(gateways.verify_paypal_webhook(headers, "{}"))

    def test_stripe_checkout_for_our_own_session_assigns_by_plan_slug(self):
        import hashlib, hmac, time
        save_stripe()
        event = {"id": "evt_9", "type": "checkout.session.completed", "data": {"object": {
            "payment_status": "paid", "client_reference_id": str(self.user.pk), "customer": "cus_1",
            "metadata": {"plan_slug": "pro"}}}}
        body = json.dumps(event).encode()
        stamp = int(time.time())
        sig = hmac.new(b"whsec_abc", f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
        r = self.client.post("/webhooks/stripe/", body, content_type="application/json",
                             HTTP_STRIPE_SIGNATURE=f"t={stamp},v1={sig}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(UserAccess.objects.get(user=self.user).plan, self.pro)


class AdminPageTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("root", "r@x.test", "pw-Admin-1234")
        self.client.force_login(self.admin)

    def post(self, kind, **data):
        return self.client.post("/control/", {"form_kind": kind, **{f"{kind}-{k}": v for k, v in data.items()}})

    def test_cards_save_and_show_status_without_showing_secrets(self):
        self.post("stripe", enabled="on", restricted_key="rk_test_zzz", webhook_secret="whsec_zzz")
        page = self.client.get("/control/").content.decode()
        self.assertIn("Ready for checkout", page)
        self.assertNotIn("rk_test_zzz", page)
        self.assertIn("/webhooks/stripe/", page)
        self.assertIn("/webhooks/paypal/", page)

    def test_wrong_prefix_is_refused(self):
        self.post("stripe", enabled="on", restricted_key="sk" + "_live_" + "wrongfield", webhook_secret="whsec_zzz")
        self.assertFalse(gateways.status("stripe")["credentials"])

    def test_only_the_platform_admin_sees_or_saves_payment_settings(self):
        staff = User.objects.create_user("staff", "s@x.test", "pw-User-1234", is_staff=True)
        c = self.client_class()
        c.force_login(staff)
        self.assertNotIn("w2-admin-stripe-title", c.get("/control/").content.decode())
        c.post("/control/", {"form_kind": "stripe", "stripe-enabled": "on", "stripe-restricted_key": "rk_test_x"})
        self.assertFalse(gateways.status("stripe")["credentials"])
        self.assertEqual(c.post("/control/payments/stripe/test/").status_code, 302)

    def test_test_button_reports_the_result_and_use_environment_button_works(self):
        save_stripe()
        with mock.patch(CALL, return_value=(401, {"error": {"message": "Invalid API Key"}})):
            page = self.client.post("/control/payments/stripe/test/", follow=True)
        self.assertContains(page, "Invalid API Key")
        self.client.post("/control/payments/stripe/environment/")
        self.assertEqual(gateways.status("stripe")["source"], "environment")

    def test_plan_save_verifies_each_mapping_with_the_gateway(self):
        save_stripe()
        plan = Plan.objects.create(slug="pro", name="Pro")
        m = PlanProviderMapping.objects.create(plan=plan, provider="stripe", external_id="price_123")
        with mock.patch(CALL, return_value=(200, {"active": True, "recurring": {"interval": "month"}})):
            admin_form = {"name": "Pro", "slug": "pro", "description": "", "price_label": "", "sort_order": 0,
                          "features": [], "limits": "{}", "stripe_link": "", "stripe_payment_link_id": "",
                          "paypal_link": "",
                          "provider_mappings-TOTAL_FORMS": 1, "provider_mappings-INITIAL_FORMS": 1,
                          "provider_mappings-MIN_NUM_FORMS": 0, "provider_mappings-MAX_NUM_FORMS": 1000,
                          "provider_mappings-0-id": m.pk, "provider_mappings-0-plan": plan.pk,
                          "provider_mappings-0-provider": "stripe", "provider_mappings-0-external_id": "price_123"}
            self.client.post(f"/django-admin/tenancy/plan/{plan.pk}/change/", admin_form)
        m.refresh_from_db()
        self.assertIsNotNone(m.verified_at)
