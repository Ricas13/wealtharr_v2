"""Synthetic, read-only per-instrument market-data status regression tests."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from tracker.models import Account, LedgerEntry, MarketObservation, PriceMark, StrategyInstance, StrategyPreset, YahooSync
from tracker.price_health import price_health
from tracker.services import validate_and_record


class PriceHealthUITests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user("health-owner", password="synthetic-123456")
        self.other = User.objects.create_user("health-other", password="synthetic-123456")
        preset, _ = StrategyPreset.objects.get_or_create(
            slug="nasdaq-quarterly-target",
            defaults={"name": "9Sig", "risk": "HIGH", "summary": "Test", "active": True})
        account = Account.objects.create(owner=self.owner, name="ISA", kind="ISA", broker="TRADING212", country="GB")
        self.strategy = StrategyInstance.objects.create(
            owner=self.owner, account=account, preset=preset, name="Price evidence",
            equity_symbol="LQQ3.L", safe_symbol="ERNS.L", market_data_enabled=True)
        self.client.force_login(self.owner)

    def quote(self, symbol, price, *, age=2, source="yahoo", currency="GBP"):
        day = timezone.localdate() - timedelta(days=age)
        PriceMark.objects.update_or_create(strategy=self.strategy, symbol=symbol,
            defaults={"price": Decimal(price), "as_of": day, "source": source})
        MarketObservation.objects.update_or_create(symbol=symbol, as_of=day,
            defaults={"price": Decimal(price), "currency": currency, "source": "yahoo",
                      "raw_quote_unit": "GBP", "raw_close": Decimal(price), "raw_unit_verified": True})

    def holding(self):
        day = timezone.localdate() - timedelta(days=40)
        validate_and_record(self.strategy, LedgerEntry(kind="DEPOSIT", amount=Decimal("1000"), occurred_on=day))
        validate_and_record(self.strategy, LedgerEntry(
            kind="BUY", symbol="LQQ3.L", quantity=Decimal("1"),
            unit_price=Decimal("100"), occurred_on=day))

    def url(self, page="today"):
        return reverse("v2_instance_page", args=[self.strategy.pk, page])

    def test_latest_single_quote_never_makes_other_missing_fund_fresh(self):
        self.quote("LQQ3.L", "101")
        result = price_health(self.strategy)
        self.assertEqual(result["state"], "attention")
        self.assertEqual([r["state"] for r in result["rows"]], ["ok", "missing"])
        for section in ("today", "study", "ledger", "history", "strategy"):
            with self.subTest(section=section):
                page = self.client.get(self.url(section))
                self.assertEqual(page.status_code, 200)
                self.assertContains(page, "Prices need attention")
                self.assertNotContains(page, "Fund closes current")
        self.assertContains(self.client.get(reverse("v2_overview")), "Prices need attention")

    def test_blocked_today_explains_missing_fund_without_trading_advice(self):
        self.holding()
        self.quote("LQQ3.L", "102")
        page = self.client.get(self.url())
        self.assertContains(page, "Cannot compute a reliable instruction")
        self.assertContains(page, "Price check details")
        self.assertContains(page, "ERNS.L")
        self.assertContains(page, "No accepted close")
        self.assertContains(page, "next scheduled check")
        self.assertNotContains(page, "Review the verified quarterly proposal")

    def test_accepted_quotes_require_both_funds_and_matching_currency(self):
        self.quote("LQQ3.L", "101")
        self.quote("ERNS.L", "52")
        self.assertEqual(price_health(self.strategy)["state"], "ok")
        self.assertContains(self.client.get(self.url()), "Fund closes current")
        MarketObservation.objects.filter(symbol="ERNS.L").update(currency="USD")
        self.assertEqual(price_health(self.strategy)["rows"][1]["state"], "unverified")
        self.assertNotContains(self.client.get(self.url()), "Fund closes current")

    def test_mismatched_stale_and_manual_quotes_never_turn_green(self):
        self.quote("LQQ3.L", "101", age=2)
        self.quote("ERNS.L", "52", age=5)
        self.assertEqual([r["state"] for r in price_health(self.strategy)["rows"]], ["mismatch", "mismatch"])
        self.quote("LQQ3.L", "101", age=12)
        self.quote("ERNS.L", "52", source="manual")
        self.assertEqual([r["state"] for r in price_health(self.strategy)["rows"]], ["stale", "unverified"])

    def test_reference_price_hold_prevents_false_green(self):
        self.quote("LQQ3.L", "101")
        self.quote("ERNS.L", "52")
        from tracker.strategies.registry import module_for
        refs = module_for(self.strategy).reference_symbols
        refs = refs(self.strategy) if callable(refs) else refs
        self.assertTrue(refs)
        YahooSync.objects.create(symbol=refs[0], last_error="PRICE_HOLD:REVISED_CLOSE",
                                 last_attempt=timezone.now())
        self.assertEqual(price_health(self.strategy)["state"], "hold")
        self.assertContains(self.client.get(self.url()), "Price held for review")

    def test_tracking_only_is_not_reported_current(self):
        self.quote("LQQ3.L", "101")
        self.quote("ERNS.L", "52")
        self.strategy.tracking_only = True
        self.strategy.save(update_fields=["tracking_only"])
        self.assertEqual(price_health(self.strategy)["state"], "off")
        self.assertContains(self.client.get(self.url()), "Price monitoring off")

    def test_isolation_and_get_does_not_modify_ledger(self):
        self.holding()
        self.quote("LQQ3.L", "101")
        self.quote("ERNS.L", "52")
        before = self.strategy.ledger.count()
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.url()).status_code, 404)
        self.assertNotContains(self.client.get(reverse("v2_overview")), "Price evidence")
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(self.url()).status_code, 200)
        self.assertEqual(self.strategy.ledger.count(), before)
