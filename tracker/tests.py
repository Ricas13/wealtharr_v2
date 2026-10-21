from unittest.mock import patch
from .models import NotificationPreference, SystemIntegration
from .services import dispatch, encrypt_secret, decrypt_secret

from datetime import date, timedelta
from decimal import Decimal as D
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from .forms import LedgerForm
from .models import Account, LedgerEntry, PriceMark, StrategyInstance, StrategyPreset, WealthSnapshot
from .services import consolidated, ledger_summary, validate_and_record

TODAY = date(2026, 10, 18)

class LedgerTests(TestCase):
    def setUp(self):
        U = get_user_model()
        self.user = U.objects.create_user("owner", "owner@example.com", "a-very-long-test-password")
        self.isa = Account.objects.create(owner=self.user,name="ISA",kind="ISA")
        self.gia = Account.objects.create(owner=self.user,name="Trading",kind="GIA")
        self.preset = StrategyPreset.objects.get(slug="nasdaq-quarterly-target")
        self.one = StrategyInstance.objects.create(owner=self.user,account=self.isa,preset=self.preset,name="9Sig - ISA")
        self.two = StrategyInstance.objects.create(owner=self.user,account=self.gia,preset=self.preset,name="9Sig - Trading")
    def entry(self,strategy,kind,when=TODAY,**data):
        return validate_and_record(strategy,LedgerEntry(strategy=strategy,kind=kind,occurred_on=when,**data))
    def test_independent_strategy_tabs_and_no_fabricated_prices(self):
        self.entry(self.one,"DEPOSIT",amount=D("1000"))
        self.entry(self.one,"BUY",symbol="LQQ3",quantity=D("4"),unit_price=D("100"),fee=D("1"))
        self.entry(self.two,"DEPOSIT",amount=D("500"))
        self.assertEqual(ledger_summary(self.one)["cash"],D("599.00"))
        self.assertEqual(ledger_summary(self.two)["cash"],D("500.00"))
        self.assertIsNone(ledger_summary(self.one)["value"])
        self.assertIsNone(consolidated(self.user)["total"])
        PriceMark.objects.create(strategy=self.one,symbol="LQQ3",price=D("120"),as_of=TODAY)
        self.assertEqual(ledger_summary(self.one)["value"],D("1079.00"))
        self.assertEqual(consolidated(self.user)["total"],D("1579.00"))
        self.assertEqual(consolidated(self.user)["gain"],D("79.00"))
    def test_sale_cash_and_contributions(self):
        self.entry(self.one,"DEPOSIT",amount=D("1000"))
        self.entry(self.one,"BUY",symbol="VWRP",quantity=D("5"),unit_price=D("50"),fee=D("2"))
        self.entry(self.one,"SELL",symbol="VWRP",quantity=D("2"),unit_price=D("65"),fee=D("1"))
        self.entry(self.one,"WITHDRAW",amount=D("100"))
        report=ledger_summary(self.one)
        self.assertEqual(report["cash"],D("777.00"))
        self.assertEqual(report["positions"]["VWRP"],D("3"))
        self.assertEqual(report["net_contributed"],D("900.00"))
    def test_cannot_overdraft_oversell_or_backdate(self):
        with self.assertRaisesMessage(ValidationError,"Not enough"):
            self.entry(self.one,"BUY",symbol="QQQ3",quantity=D("1"),unit_price=D("5"))
        self.entry(self.one,"DEPOSIT",amount=D("100"))
        with self.assertRaisesMessage(ValidationError,"Cannot sell"):
            self.entry(self.one,"SELL",symbol="QQQ3",quantity=D("1"),unit_price=D("5"))
        with self.assertRaisesMessage(ValidationError,"exceeds"):
            self.entry(self.one,"WITHDRAW",amount=D("101"))
        # A back-dated buy is refused when cash didn't exist yet on that date.
        with self.assertRaisesMessage(ValidationError,"cash negative at an earlier"):
            self.entry(self.one,"BUY",when=TODAY-timedelta(days=1),symbol="QQQ3",
                       quantity=D("1"),unit_price=D("5"))
        # A back-dated deposit keeps every balance valid, so it is accepted.
        self.entry(self.one,"DEPOSIT",when=TODAY-timedelta(days=1),amount=D("5"))
        self.assertEqual(ledger_summary(self.one)["cash"],D("105.00"))
    def test_invalid_form_and_cross_owner(self):
        form=LedgerForm(data={"kind":"BUY","occurred_on":TODAY,"symbol":"QQQ3",
                              "quantity":"1","unit_price":"30","amount":"5"})
        self.assertFalse(form.is_valid())
        other=get_user_model().objects.create_user("other",password="other-long-valid-password")
        wrong=StrategyInstance(owner=other,account=self.isa,preset=self.preset,name="Bad")
        with self.assertRaises(ValidationError): wrong.full_clean()

class SiteTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_superuser("admin","admin@example.com","a-very-long-test-password")
        self.account=Account.objects.create(owner=self.user,name="ISA",kind="ISA")
    @override_settings(TURNSTILE_REQUIRED=False)
    def test_login_and_empty_chart(self):
        self.assertEqual(self.client.get(reverse("v2_overview")).status_code,302)
        self.assertEqual(self.client.post(reverse("login"),{"username":"admin","password":"a-very-long-test-password"}).status_code,302)
        overview = self.client.get(reverse("v2_overview"))
        self.assertEqual(overview.status_code, 200)
        self.assertIsNone(overview.context["overview_total"])
    @override_settings(TURNSTILE_REQUIRED=True,TURNSTILE_SECRET_KEY="",TURNSTILE_SITE_KEY="")
    def test_turnstile_fail_closed(self):
        self.client.post(reverse("login"),{"username":"admin","password":"a-very-long-test-password"})
        self.assertNotIn("_auth_user_id",self.client.session)
    def test_owner_scoped_endpoints(self):
        other=get_user_model().objects.create_user("other",password="other-long-valid-password")
        s=StrategyInstance.objects.create(owner=other,account=Account.objects.create(owner=other,name="Other"),
                                         preset=StrategyPreset.objects.first(),name="Private")
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("v2_instance_page", args=[s.pk, "today"])).status_code,404)
        self.assertEqual(self.client.post(reverse("v2_ledger_add", args=[s.pk]),{}).status_code,404)
    def test_create_account_strategy_deposit_and_snapshot(self):
        self.client.force_login(self.user)
        url = reverse("v2_add_strategy")
        response = self.client.post(url, {
            "strategy": "nasdaq-quarterly-target", "platform": "TRADING212", "account_type": "ISA", "ack": "yes",
            "mode": "fresh", "name": "9Sig",
            "start": timezone.localdate().isoformat(),
            "amount": "1000", "deposited": "yes",
        })
        s = StrategyInstance.objects.get(name="9Sig")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(s.account.kind, "ISA")
        self.assertEqual(s.account.broker, "TRADING212")
        self.assertEqual(ledger_summary(s)["cash"], D("1000"))
        self.assertFalse(s.ledger.filter(kind="BUY").exists())
        page = self.client.get(reverse("v2_instance_page", args=[s.pk, "today"]))
        self.assertEqual(page.context["today_cash"], D("1000"))
        # v2 never fabricates a value snapshot merely by opening a page.
        self.assertFalse(s.value_snapshots.exists())
        self.assertFalse(WealthSnapshot.objects.filter(owner=self.user).exists())



class NotificationTests(TestCase):
    def setUp(self):
        self.owner=get_user_model().objects.create_user("alertowner","owner@example.com","a-very-long-test-password")
        self.prefs=NotificationPreference.objects.create(
            owner=self.owner,destination_email="owner@example.com",email_enabled=True,discord_enabled=True)
        self.config=SystemIntegration.current()
        self.config.smtp_host="smtp.example.test"
        self.config.smtp_sender="alerts@example.test"
        self.config.smtp_password_ciphertext=encrypt_secret("smtp-secret-not-for-logs")
        self.config.discord_url_ciphertext=encrypt_secret("https://discord.com/api/webhooks/123456/test-secret")
        self.config.save()
    def test_encryption_roundtrip(self):
        self.assertNotIn("smtp-secret-not-for-logs", self.config.smtp_password_ciphertext)
        self.assertEqual(decrypt_secret(self.config.smtp_password_ciphertext),"smtp-secret-not-for-logs")
    def test_idempotent_email_delivery(self):
        with patch("tracker.services.EmailMessage.send",return_value=1) as sender:
            self.assertEqual(dispatch(self.owner,"event-1","Test","Hello",channels=("email",))["email"],"sent")
            self.assertEqual(dispatch(self.owner,"event-1","Test","Hello",channels=("email",))["email"],"sent")
            self.assertEqual(sender.call_count,1)
    def test_failed_delivery_retries_after_backoff_without_duplicate_send(self):
        from datetime import timedelta
        from django.utils import timezone
        from tracker.models import NotificationDelivery
        with patch("tracker.services.EmailMessage.send", side_effect=OSError("transport down")) as failed:
            self.assertEqual(dispatch(self.owner, "retry-1", "Test", "Hello",
                                      channels=("email",))["email"], "retrying")
            self.assertEqual(dispatch(self.owner, "retry-1", "Test", "Hello",
                                      channels=("email",))["email"], "retrying")
            self.assertEqual(failed.call_count, 1)
        delivery = NotificationDelivery.objects.get(owner=self.owner, channel="email",
                                                     event_key="retry-1")
        self.assertEqual(delivery.attempts, 1)
        delivery.next_retry_at = timezone.now() - timedelta(minutes=1)
        delivery.save(update_fields=["next_retry_at"])
        with patch("tracker.services.EmailMessage.send", return_value=1) as sender:
            from django.core.management import call_command
            call_command("retry_notifications", verbosity=0)
            self.assertEqual(sender.call_count, 1)
            self.assertEqual(dispatch(self.owner, "retry-1", "Test", "Hello",
                                      channels=("email",))["email"], "sent")
            self.assertEqual(sender.call_count, 1)
        delivery.refresh_from_db()
        self.assertEqual(delivery.attempts, 2)
        self.assertEqual(delivery.status, "SENT")

    def test_discord_delivery_payload_does_not_leak_webhook(self):
        class Response:
            status=204
            def __enter__(self): return self
            def __exit__(self,*args): pass
        with patch("tracker.services.urlopen",return_value=Response()) as send:
            self.assertEqual(dispatch(self.owner,"event-2","Test","Hello",channels=("discord",))["discord"],"sent")
            data=send.call_args.args[0].data.decode()
            self.assertIn("Hello",data)
            self.assertNotIn("test-secret",data)

class TemplateAndSecurityTests(TestCase):
    def setUp(self):
        self.owner=get_user_model().objects.create_superuser("viewowner","owner@example.com","a-very-long-test-password")
        self.account=Account.objects.create(owner=self.owner,name="Personal ISA")
        self.strategy=StrategyInstance.objects.create(
            owner=self.owner,account=self.account,preset=StrategyPreset.objects.first(),name="9Sig ISA")
    def test_admin_login_redirects_to_challenged_login(self):
        response=self.client.get(reverse("admin:login"))
        self.assertEqual(response.status_code,302)
        self.assertTrue(response.url.startswith(reverse("login")))
    def test_all_owner_pages_render(self):
        self.client.force_login(self.owner)
        for url in (reverse("v2_overview"),
                    reverse("v2_instance_page", args=[self.strategy.pk, "strategy"]),
                    reverse("v2_settings"), reverse("change_password"), reverse("control")):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code,200)
        self.assertEqual(self.client.get("/app/strategies/new/").status_code, 404)
    def test_snapshot_is_not_saved_when_price_is_missing(self):
        self.client.force_login(self.owner)
        validate_and_record(self.strategy,LedgerEntry(kind="DEPOSIT",amount=D("200"),occurred_on=timezone.localdate()))
        validate_and_record(self.strategy,LedgerEntry(kind="BUY",symbol="LQQ3",quantity=D("1"),
                                                     unit_price=D("50"),occurred_on=timezone.localdate()))
        self.client.get(reverse("v2_instance_page", args=[self.strategy.pk, "today"]))
        self.assertFalse(self.strategy.value_snapshots.exists())
        self.assertFalse(WealthSnapshot.objects.filter(owner=self.owner).exists())
