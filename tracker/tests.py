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
        with self.assertRaisesMessage(ValidationError,"chronological"):
            self.entry(self.one,"DEPOSIT",when=TODAY-timedelta(days=1),amount=D("5"))
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
        self.assertEqual(self.client.get(reverse("dashboard")).status_code,302)
        self.assertEqual(self.client.post(reverse("login"),{"username":"admin","password":"a-very-long-test-password"}).status_code,302)
        self.assertContains(self.client.get(reverse("dashboard")),"wealth-chart-points")
    @override_settings(TURNSTILE_REQUIRED=True,TURNSTILE_SECRET_KEY="",TURNSTILE_SITE_KEY="")
    def test_turnstile_fail_closed(self):
        self.client.post(reverse("login"),{"username":"admin","password":"a-very-long-test-password"})
        self.assertNotIn("_auth_user_id",self.client.session)
    def test_owner_scoped_endpoints(self):
        other=get_user_model().objects.create_user("other",password="other-long-valid-password")
        s=StrategyInstance.objects.create(owner=other,account=Account.objects.create(owner=other,name="Other"),
                                         preset=StrategyPreset.objects.first(),name="Private")
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("strategy_detail",args=[s.pk])).status_code,404)
        self.assertEqual(self.client.post(reverse("record_entry",args=[s.pk]),{}).status_code,404)
    def test_create_account_strategy_deposit_and_snapshot(self):
        self.client.force_login(self.user)
        p=StrategyPreset.objects.get(slug="nasdaq-quarterly-target")
        response=self.client.post(reverse("new_strategy"),{"name":"9Sig ISA","account":self.account.pk,
                          "preset":p.pk,"monthly_target":"500","contribution_alerts":"on"})
        self.assertEqual(response.status_code,302)
        s=StrategyInstance.objects.get(name="9Sig ISA")
        response=self.client.post(reverse("record_entry",args=[s.pk]),{
                          "kind":"DEPOSIT","occurred_on":timezone.localdate().isoformat(),"amount":"1000"})
        self.assertEqual(response.status_code,302)
        self.assertEqual(ledger_summary(s)["cash"],D("1000"))
        self.client.post(reverse("capture_snapshot"))
        self.assertEqual(WealthSnapshot.objects.get(owner=self.user).total_value,D("1000"))
