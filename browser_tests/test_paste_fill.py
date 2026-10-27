"""The paste helper reads common confirmation layouts and never guesses."""
from pathlib import Path

from django.conf import settings
from django.test import override_settings

from browser_tests.harness import BrowserHarness


@override_settings(TURNSTILE_REQUIRED=False)
class PasteParserBrowserTests(BrowserHarness):
    def setUp(self):
        super().setUp()
        self.new_page(1024)
        self.sign_in()
        self.visit("v2_overview")
        self.page.add_script_tag(path=str(Path(settings.BASE_DIR) / "static/js/paste_fill.js"))

    def parse(self, text, today="2026-10-30"):
        return self.page.evaluate("([t, d]) => window.WealtharrPaste.parse(t, d)", [text, today])

    def test_a_typical_confirmation(self):
        got = self.parse("Order filled\nDate: 12/10/2026\nQuantity: 12.5\nPrice: £84.20\nFee: £0.00")
        self.assertEqual((got["date"], got["quantity"], got["price"], got["fee"]),
                         ("2026-10-12", 12.5, 84.2, 0))
        self.assertIn("day/month/year", " ".join(got["notes"]))

    def test_iso_and_written_dates_and_thousands_separators(self):
        got = self.parse("Executed on 12 Oct 2026 · 1,250.5 shares @ $1,234.56 · Total fees: 1.50")
        self.assertEqual((got["date"], got["quantity"], got["price"], got["fee"]),
                         ("2026-10-12", 1250.5, 1234.56, 1.5))
        self.assertEqual(self.parse("2026-10-05 Quantity 3 Price 10")["date"], "2026-10-05")

    def test_pence_prices_are_converted_and_say_so(self):
        got = self.parse("Quantity: 4\nAverage price: 8,420.5p")
        self.assertEqual(got["price"], 84.205)
        self.assertIn("pence", " ".join(got["notes"]))

    def test_several_fee_lines_are_added_and_say_so(self):
        got = self.parse("Quantity: 2\nPrice: 10\nCommission: 1.00\nStamp duty: 0.50")
        self.assertEqual(got["fee"], 1.5)
        self.assertIn("fee lines", " ".join(got["notes"]))

    def test_future_dates_and_ambiguous_numbers_are_not_used(self):
        got = self.parse("Date: 2027-01-01\nQuantity: 12,5\nPrice: 10", today="2026-10-30")
        self.assertIsNone(got["date"])
        self.assertIsNone(got["quantity"])
        self.assertEqual(got["price"], 10)
        self.assertIn("future", " ".join(got["notes"]))

    def test_nothing_recognisable_fills_nothing(self):
        got = self.parse("Thanks for your order! We'll be in touch.")
        self.assertEqual([got["date"], got["quantity"], got["price"], got["fee"]], [None] * 4)
