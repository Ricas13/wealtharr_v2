"""Alert deep link and the fill-form helpers."""
from datetime import timedelta
from decimal import Decimal as D
from unittest.mock import patch

from django.test import TestCase, override_settings

from tracker.cockpit_notifications import _record_link
from tracker.test_review_fixes import make_instance


class RecordLinkTests(TestCase):
    def test_link_points_at_the_review_fill_form_and_carries_no_figures(self):
        from tracker.models import StrategySignal
        from django.utils import timezone
        strategy = make_instance("link-owner", "nasdaq-quarterly-target")
        signal = StrategySignal.objects.create(
            strategy=strategy, signal_key="link:1", kind="QUARTERLY",
            as_of=timezone.localdate() - timedelta(days=1), title="t", message="m",
            actions=[{"side": "BUY", "symbol": "ERNS.L", "quantity": "5", "estimated_gbp": "250"}])
        with override_settings(SITE_URL="https://app.example.com"):
            text = _record_link(strategy, signal)
        self.assertIn("https://app.example.com/s/%d/review/%d/#w2-fill-0" % (strategy.pk, signal.pk), text)
        self.assertNotIn("250", text)
        with override_settings(SITE_URL=""):
            self.assertEqual(_record_link(strategy, signal), "")
