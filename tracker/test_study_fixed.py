"""Study for fixed-weight strategies: stand-ins before launch, yearly rule replay, page."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from tracker.models import Account, StrategyInstance, StrategyPreset
from tracker.strategies.registry import module_for_key
from tracker.test_study import TODAY, seed


def seed_all_weather(start, fund_start, gold=lambda d, i: 50.0):
    """Stand-ins from ``start``; the real funds only from ``fund_start``."""
    seed("GBPUSD=X", start, lambda d, i: 1.25)
    seed("SPY", start, lambda d, i: 400 + i * 0.5)
    seed("VTI", start, lambda d, i: 200.0)
    seed("GLD", start, lambda d, i: 150.0)
    seed("DBC", start, lambda d, i: 20.0)
    seed("IGLT.L", start, lambda d, i: 12.0, currency="GBP")
    seed("VUAG.L", fund_start, lambda d, i: 80 + i * 0.1, currency="GBP")
    seed("GLTL.L", fund_start, lambda d, i: 30.0, currency="GBP")
    seed("SGLN.L", fund_start, gold, currency="GBP")
    seed("CMOP.L", fund_start, lambda d, i: 5.0, currency="GBP")


class FixedWeightStudyEngineTests(TestCase):
    def setUp(self):
        cache.clear()
        self.study = module_for_key("all-weather").study

    def test_every_released_strategy_has_a_study(self):
        from tracker.strategies.registry import _plugins
        for key, plugin in _plugins().items():
            self.assertIsNotNone(plugin.study, "%s needs a Study (docs/adding-a-strategy.md)" % key)

    def test_stand_ins_join_the_real_fund_and_are_flagged(self):
        start = TODAY - timedelta(days=800)
        seed_all_weather(start, TODAY - timedelta(days=200))
        data = self.study.load_data()
        self.assertTrue(data.ready)
        self.assertEqual(data.days[0], start)
        # Gold's stand-in (GLD, in USD) is converted and scaled to SGLN's first price.
        self.assertAlmostEqual(data.prices["gold"][0], 50.0, places=6)
        result = self.study.run(data, "1y", 1000, 0)
        self.assertTrue(any("SGLN" in note and "GLD" in note for note in result["simulated_notes"]))
        recent = self.study.run(data, "1m", 1000, 0)
        self.assertEqual(recent["simulated_notes"], [])

    def test_yearly_rule_replay_rebalances_after_a_rally(self):
        start = TODAY - timedelta(days=800)
        # Gold triples in the middle, so a year-end review must sell some.
        seed_all_weather(start, start, gold=lambda d, i: 50.0 if i < 200 else 150.0)
        data = self.study.load_data()
        result = self.study.run(data, "1y", 10000, 100)
        s = result["series"]["strategy"]
        # Opening buys (5) plus at least one year-end rebalance.
        self.assertGreater(s["trades"], 5)
        self.assertGreater(s["put_in"], 10000)
        self.assertIsNotNone(result["series"]["spy"])


class FixedWeightStudyPageTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = get_user_model().objects.create_user("aw-study", password="study-safe-password-1")
        preset, _ = StrategyPreset.objects.get_or_create(slug="all-weather", defaults={"name": "All Weather"})
        account = Account.objects.create(owner=self.owner, name="AW", kind="ISA", broker="TRADING212", country="GB")
        self.strategy = StrategyInstance.objects.create(
            owner=self.owner, account=account, preset=preset, name="All Weather", strategy_key="all-weather")
        self.client.force_login(self.owner)
        self.url = reverse("v2_instance_page", args=[self.strategy.pk, "study"])

    def test_empty_then_results_with_all_weather_wording(self):
        page = self.client.get(self.url)
        self.assertContains(page, "same All Weather rules")
        self.assertContains(page, "Study data not downloaded yet")
        self.assertNotContains(page, "isn't available for this strategy")
        seed_all_weather(TODAY - timedelta(days=800), TODAY - timedelta(days=200))
        cache.clear()
        page = self.client.get(self.url + "?range=1y&amount=2000")
        self.assertContains(page, "All Weather value now")
        self.assertContains(page, "Includes simulated history")
        self.assertContains(page, "SPY (S&amp;P 500)")
        self.assertNotContains(page, "9Sig")
