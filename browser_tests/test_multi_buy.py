"""One form for several recommended purchases, on a phone."""
from datetime import timedelta
from decimal import Decimal as D

from django.urls import reverse
from django.utils import timezone

from browser_tests.test_v2_phase3 import Phase3LedgerBrowserTests as Fixture
from tracker.models import LedgerEntry
from tracker.services import validate_and_record


class MultiBuyBrowserTests(Fixture):
    def test_two_funds_one_form_one_save(self):
        validate_and_record(self.instance, LedgerEntry(
            kind="DEPOSIT", amount=D("1000"), occurred_on=timezone.localdate() - timedelta(days=2)))
        self.new_page(390)
        self.sign_in()
        self.page.goto(self.live_server_url + reverse("v2_ledger_add", args=[self.instance.pk])
                       + "?kind=BUY&symbol=LQQ3.L&budget=600.00&symbol=ERNS.L&budget=400.00",
                       wait_until="networkidle")
        self.assertEqual(self.page.locator("fieldset.w2-buy-row").count(), 2)
        self.assertLessEqual(self.page.evaluate("document.documentElement.scrollWidth"), 391)
        for box in self.page.evaluate("""() => [...document.querySelectorAll('#w2-buys-form input:not([type=hidden])')]
                .map(el => { const r = el.getBoundingClientRect(); return [r.left, r.right]; })"""):
            self.assertGreaterEqual(box[0], 0)
            self.assertLessEqual(box[1], 390)
        self.page.locator("[name=quantity_0]").fill("6")
        self.page.locator("[name=unit_price_0]").fill("100")
        self.page.locator("[name=quantity_1]").fill("8")
        self.page.locator("[name=unit_price_1]").fill("50")
        self.page.get_by_role("button", name="Save purchases").click()
        self.page.wait_for_url("**/today/")
        self.assertEqual(
            sorted(LedgerEntry.objects.filter(strategy=self.instance, kind="BUY").values_list("symbol", flat=True)),
            ["ERNS.L", "LQQ3.L"])
        self.assertEqual(self.browser_errors, [])


for _name in [n for n in dir(Fixture) if n.startswith("test_")]:
    setattr(MultiBuyBrowserTests, _name, None) if _name not in MultiBuyBrowserTests.__dict__ else None
del Fixture
