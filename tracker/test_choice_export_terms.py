"""Terms page, one-download export and the explicit "I chose this" step."""
import csv
import io
import zipfile
from datetime import timedelta
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from tracker.core.catalogue import CATALOGUE_VERSION, onboarding_manifest, released_version
from tracker.core.presentation import NOT_ADVICE
from tracker.models import Account, LedgerEntry, StrategyInstance, StrategyPreset
from tracker.services import validate_and_record


def _strategy(owner, name):
    preset, _ = StrategyPreset.objects.get_or_create(
        slug="nasdaq-quarterly-target",
        defaults={"name": "9Sig", "risk": "HIGH", "summary": "Fixed rules", "active": True})
    account = Account.objects.create(owner=owner, name=name + " ISA", kind="ISA", country="GB",
                                     broker="TRADING212")
    return StrategyInstance.objects.create(
        owner=owner, account=account, preset=preset, name=name,
        strategy_key="nasdaq-quarterly-target", platform_code="TRADING212", account_type_code="ISA",
        started_on=timezone.localdate() - timedelta(days=10), owner_checked_on=timezone.localdate(),
        strategy_version=released_version("nasdaq-quarterly-target"), mapping_version=CATALOGUE_VERSION,
        resolved_instruments=onboarding_manifest("nasdaq-quarterly-target", "TRADING212", "ISA"),
        equity_symbol="LQQ3.L", safe_symbol="ERNS.L", tracking_only=True)


class ExportTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("exp-owner", password="x" * 12)
        self.other = get_user_model().objects.create_user("exp-other", password="x" * 12)
        self.mine = _strategy(self.owner, "Mine")
        self.theirs = _strategy(self.other, "SECRET_OTHER_STRATEGY")
        day = timezone.localdate() - timedelta(days=5)
        validate_and_record(self.mine, LedgerEntry(
            kind="DEPOSIT", amount=D("500"), occurred_on=day, note="=HYPERLINK(\"x\")"))
        validate_and_record(self.theirs, LedgerEntry(kind="DEPOSIT", amount=D("999"), occurred_on=day))

    def test_login_required(self):
        self.assertEqual(self.client.get(reverse("v2_export")).status_code, 302)

    def test_zip_holds_only_the_owners_records_and_the_rules(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("v2_export"))
        self.assertEqual(response["Content-Type"], "application/zip")
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        names = set(archive.namelist())
        folder = "strategy-%d/" % self.mine.pk
        self.assertEqual(names, {"README.txt"} | {folder + n for n in (
            "ledger.csv", "reviews.csv", "fills.csv", "rules.csv", "prices.csv")})
        self.assertNotIn("strategy-%d/ledger.csv" % self.theirs.pk, names)
        self.assertIn(NOT_ADVICE, archive.read("README.txt").decode())
        ledger = list(csv.reader(io.StringIO(archive.read(folder + "ledger.csv").decode())))
        self.assertEqual(ledger[1][4], "DEPOSIT")
        self.assertEqual(ledger[1][8], "500.00")
        self.assertTrue(ledger[1][10].startswith("'="), "spreadsheet formulas are neutralised")
        rules = archive.read(folder + "rules.csv").decode()
        self.assertIn("target_weight", rules)
        self.assertIn("LQQ3.L", rules)
        for name in names:
            self.assertNotIn("SECRET_OTHER", archive.read(name).decode())

    def test_settings_and_about_link_to_the_download(self):
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("v2_settings")), reverse("v2_export"))
        self.assertContains(self.client.get(reverse("v2_about")), reverse("v2_export"))


class TermsTests(TestCase):
    def test_terms_are_public_indexable_and_name_no_strategy_brand(self):
        page = self.client.get(reverse("terms"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Not financial advice")
        self.assertContains(page, "not affiliated with, endorsed by or sponsored by")
        self.assertNotContains(page, "9Sig")
        self.assertNotIn("noindex", page.get("X-Robots-Tag", ""))
        self.assertContains(self.client.get("/sitemap.xml"), "/terms/")
        self.assertContains(self.client.get("/"), 'href="/terms/"')


class ChoiceAcknowledgementTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("choice-owner", password="x" * 12)
        self.client.force_login(self.user)

    def test_off_by_default_the_flow_is_unchanged(self):
        self.assertNotContains(self.client.get(reverse("v2_add_strategy")), 'name="ack_choice"')

    @override_settings(REQUIRE_CHOICE_ACK=True)
    def test_when_required_the_box_is_shown_with_the_not_a_recommendation_note(self):
        page = self.client.get(reverse("v2_add_strategy"))
        self.assertContains(page, 'name="ack_choice"')
        self.assertContains(page, "I chose this strategy myself")


class SecurityTxtTests(TestCase):
    @override_settings(CONTACT_EMAIL="security@example.test", SITE_URL="http://testserver")
    def test_security_txt_has_contact_expiry_and_canonical(self):
        body = self.client.get("/.well-known/security.txt").content.decode()
        self.assertIn("Contact: mailto:security@example.test", body)
        self.assertIn("Expires: ", body)
        self.assertIn("Canonical: http://testserver/.well-known/security.txt", body)

    @override_settings(CONTACT_EMAIL="")
    def test_absent_without_a_contact(self):
        self.assertEqual(self.client.get("/.well-known/security.txt").status_code, 404)


class PrivacyAndTermsTests(TestCase):
    def test_privacy_covers_accounts_processors_support_and_closing(self):
        page = self.client.get(reverse("privacy"))
        for phrase in ("Your account", "Who else handles data", "Support access", "Closing your account"):
            self.assertContains(page, phrase)
        self.assertNotContains(page, "9Sig")
