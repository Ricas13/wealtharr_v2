"""Phase 1 layout cleanup: no paper trial or broker connection entrypoints."""
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import NoReverseMatch, Resolver404, resolve, reverse

from tracker.forms import AccountForm
from tracker.models import Account, LedgerEntry, StrategyInstance, StrategyPreset, StrategySignal


class RemovedPaperExperienceTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(
            username="layout-owner", password="layout-strong-password-123"
        )
        self.client.force_login(self.owner)

    def test_removed_paper_route_is_not_resolvable_or_callable(self):
        with self.assertRaises(NoReverseMatch):
            reverse("start_paper_trial")
        with self.assertRaises(Resolver404):
            resolve("/app/paper/start/")
        self.assertEqual(self.client.post("/app/paper/start/", {
            "confirm_paper": "PAPER_ONLY"
        }).status_code, 404)

    def test_real_account_forms_do_not_offer_paper(self):
        kinds = {key for key, label in Account._meta.get_field("kind").choices}
        self.assertNotIn("PAPER", kinds)
        form = AccountForm()
        html = str(form["kind"])
        self.assertNotIn("Paper tracking", html)
        self.assertNotIn('value="PAPER"', html)

    def test_historical_virtual_rows_are_preserved_but_not_presented(self):
        preset, _ = StrategyPreset.objects.get_or_create(
            slug="nasdaq-quarterly-target",
            defaults={"name":"9Sig","summary":"Fixed","risk":"HIGH","active":True},
        )
        legacy = Account.objects.create(
            owner=self.owner, name="Historical disabled account", kind="PAPER"
        )
        instance = StrategyInstance.objects.create(
            owner=self.owner, account=legacy, preset=preset,
            name="HIDDEN_LEGACY_VIRTUAL", equity_symbol="LQQ3.L",
            safe_symbol="ERNS.L",
        )
        LedgerEntry.objects.create(
            strategy=instance, kind="DEPOSIT",
            occurred_on=date(2026, 1, 3), amount=Decimal("999999.00")
        )
        StrategySignal.objects.create(
            strategy=instance, signal_key="old-virtual-alert",
            kind="QUARTERLY", as_of=date(2026, 3, 31),
            title="HIDDEN_VIRTUAL_REVIEW", message="Historical test only",
        )
        dashboard = self.client.get(reverse("dashboard"))
        self.assertEqual(dashboard.status_code, 200)
        self.assertNotContains(dashboard, "Paper portfolio")
        self.assertNotContains(dashboard, "HIDDEN_LEGACY_VIRTUAL")
        self.assertNotContains(dashboard, "HIDDEN_VIRTUAL_REVIEW")
        self.assertNotContains(dashboard, "999999")
        self.assertEqual(dashboard.context["net_contributed"], Decimal("0"))
        accounts = self.client.get(reverse("accounts"))
        self.assertNotContains(accounts, "Historical disabled account")
        self.assertTrue(Account.objects.filter(pk=legacy.pk).exists())
        self.assertTrue(LedgerEntry.objects.filter(strategy=instance).exists())
