"""The Study what-if card runs against stored prices and can be reset."""
from datetime import timedelta

from django.test import override_settings
from django.utils import timezone

from browser_tests.harness import BrowserHarness
from tracker.models import Account, StrategyInstance, StrategyPreset
from tracker.test_study import seed


@override_settings(TURNSTILE_REQUIRED=False)
class StudyWhatIfBrowserTests(BrowserHarness):
    def setUp(self):
        super().setUp()
        start = timezone.localdate() - timedelta(days=900)
        for symbol, currency in (("SPY", "USD"), ("QQQ", "USD"), ("TQQQ", "USD"), ("GBPUSD=X", "USD"),
                                 ("LQQ3.L", "GBP"), ("EQQQ.L", "GBP")):
            seed(symbol, start, lambda d, i: 100 + i * 0.05 + (i % 40) * 0.3, currency=currency)
        preset, _ = StrategyPreset.objects.get_or_create(
            slug="nasdaq-trend-dca", defaults={"name": "3x Nasdaq Trend + DCA", "risk": "HIGH", "summary": "", "active": True})
        account = Account.objects.create(owner=self.owner, name="Whatif ISA", kind="ISA", broker="TRADING212", country="GB")
        self.strategy = StrategyInstance.objects.create(
            owner=self.owner, account=account, preset=preset, name="What-if", strategy_key="nasdaq-trend-dca")

    def test_sliders_rerun_the_replay_and_reset_returns_to_the_real_rules(self):
        self.new_page(1100)
        self.sign_in()
        self.visit("v2_instance_page", self.strategy.pk, "study")
        result = self.page.locator("[data-sim-result]")
        result.get_by_text("Allocation today").wait_for()
        self.assertIn("Hypothetical", result.inner_text())
        base = result.inner_text()
        enter = self.page.locator('input[name="enter"]')
        enter.evaluate("e => { e.value = '1.12'; e.dispatchEvent(new Event('input', {bubbles: true})); }")
        self.assertEqual(self.page.locator('[data-sim-out="enter"]').inner_text(), "1.12")
        self.page.wait_for_timeout(700)
        self.page.get_by_role("button", name="Back to the real rules").click()
        self.page.wait_for_timeout(700)
        self.assertEqual(enter.input_value(), "1.04")
        self.assertEqual(result.inner_text(), base)
        self.assertEqual(self.browser_errors, [])
