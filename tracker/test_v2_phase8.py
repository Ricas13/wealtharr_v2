"""Phase 8 v2-only entrypoints and reversible IN-PLACE legacy linking.

These tests never require a real broker. No 9Sig calculations are changed.
"""
from datetime import date
from decimal import Decimal
from importlib import import_module

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from tracker.models import (
    Account, LedgerEntry, StrategyInstance, StrategyPreset,
    StrategySignal, StrategyValueSnapshot, StrategyBaseline,
)


@override_settings(TURNSTILE_REQUIRED=False)
class V2OnlyRoutingTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user("phase8_owner", password="valid-test-password-12345")
        self.other = User.objects.create_user("phase8_other", password="valid-test-password-12345")
        self.preset, _ = StrategyPreset.objects.get_or_create(
            slug="nasdaq-quarterly-target",
            defaults={"name":"9Sig", "risk":"HIGH", "summary":"Fixed", "active":True},
        )

    def add(self, user=None, *, kind="ISA", broker="TRADING212"):
        who = user or self.owner
        account = Account.objects.create(
            owner=who, name="Phase8 %s %s" % (who.pk, Account.objects.count()),
            kind=kind, broker=broker, country="GB",
        )
        return StrategyInstance.objects.create(
            owner=who, account=account, preset=self.preset, name="Existing 9Sig",
            equity_symbol="LQQ3.L", safe_symbol="ERNS.L", market_data_enabled=True,
        )

    def test_home_login_and_single_existing_instance(self):
        self.assertRedirects(self.client.get("/"), "/login/", fetch_redirect_response=False)
        self.assertEqual(self.client.get("/login/").status_code, 200)
        one = self.add()
        response = self.client.post("/login/",{
            "username": "phase8_owner", "password":"valid-test-password-12345",
        })
        target = reverse("v2_instance_page", args=[one.pk,"today"])
        self.assertRedirects(response,target,fetch_redirect_response=False)
        self.assertRedirects(self.client.get("/"),target,fetch_redirect_response=False)
        self.assertContains(self.client.get(reverse("v2_overview")),"Existing 9Sig")
        self.assertEqual(self.client.get(reverse("v2_instance_page",args=[one.pk,"ledger"])).status_code,200)
        self.add()
        self.assertRedirects(self.client.get("/"),reverse("v2_overview"),fetch_redirect_response=False)

    def test_legacy_gets_permanent_and_posts_never_execute(self):
        one = self.add()
        self.client.force_login(self.owner)
        old = ["/cockpit/", "/cockpit/history/", "/cockpit/ledger/",
               "/cockpit/strategy/", "/cockpit/settings/",
               "/app/", "/app/accounts/", "/app/settings/",
               "/app/strategies/%s/" % one.pk,
               "/app/strategies/%s/ledger/" % one.pk]
        for url in old:
            with self.subTest(url=url):
                r = self.client.get(url)
                self.assertEqual(r.status_code,301)
                self.assertIn(r["Location"].split("?")[0],
                    ("/overview/","/settings/","/s/%s/today/"%one.pk,
                     "/s/%s/history/"%one.pk,"/s/%s/ledger/"%one.pk,
                     "/s/%s/strategy/"%one.pk))
                self.assertEqual(self.client.post(url,{"amount":"9999"}).status_code,405)
        self.assertEqual(one.ledger.count(),0)

    def test_owner_scope_and_multi_account_bookmarks(self):
        a = self.add(self.owner)
        b = self.add(self.owner)
        private = self.add(self.other)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get("/cockpit/").status_code,301)
        self.assertEqual(self.client.get("/cockpit/")["Location"],"/overview/")
        self.assertEqual(self.client.get("/app/strategies/%s/" % private.pk).status_code,404)
        self.assertContains(self.client.get("/overview/"),"Existing 9Sig")
        self.assertEqual(self.client.get("/s/%s/today/" % private.pk).status_code,404)

    def test_login_restyled_and_authentication_security_retained(self):
        res = self.client.get("/login/")
        self.assertContains(res,'css/v2.css')
        self.assertContains(res,'OWNER ACCESS')
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("totp_setup")).status_code,200)
        self.assertEqual(self.client.get("/health/").status_code,200)
        self.assertEqual(self.client.get("/django-admin/").status_code,302)

    def test_migration_links_same_ids_without_spoofing_versions_or_broker(self):
        good = self.add()
        unknown = self.add(broker="UNKNOWN")
        actual = LedgerEntry.objects.create(
            strategy=good, kind="DEPOSIT",
            occurred_on=date(2026,1,5), amount=Decimal("1000"),
        )
        signal = StrategySignal.objects.create(
            strategy=good, signal_key="quarter:legacy-proof",
            kind="QUARTERLY", as_of=date(2026,3,31),
            title="Legacy saved signal", message="Recorded historic advice"
        )
        snap = StrategyValueSnapshot.objects.create(
            strategy=good, as_of=date(2026,3,31),
            total_value=Decimal("1010"), net_contributed=Decimal("1000"),
        )
        migration = import_module("tracker.migrations.0021_link_legacy_v2")
        class Apps:
            @staticmethod
            def get_model(app, model):
                return {"StrategyInstance":StrategyInstance}[model]
        migration.forwards(Apps, None)
        good.refresh_from_db()
        unknown.refresh_from_db()
        self.assertEqual(good.pk,actual.strategy_id)
        self.assertEqual(good.pk,signal.strategy_id)
        self.assertEqual(good.pk,snap.strategy_id)
        self.assertEqual(good.strategy_key,"nasdaq-quarterly-target")
        self.assertEqual(good.platform_code,"TRADING212")
        self.assertTrue(good.tracking_only)
        self.assertEqual(good.strategy_version,"")
        self.assertEqual(good.mapping_version,"")
        self.assertEqual(unknown.strategy_key,"")
        self.assertTrue(unknown.tracking_only)
        self.client.force_login(self.owner)
        self.assertContains(self.client.get("/overview/"),"Existing 9Sig")
        self.assertEqual(LedgerEntry.objects.get(pk=actual.pk).strategy_id,good.pk)
        migration.backwards(Apps, None)
        good.refresh_from_db()
        self.assertEqual(good.strategy_key,"")
        self.assertTrue(LedgerEntry.objects.filter(pk=actual.pk, strategy_id=good.pk).exists())
