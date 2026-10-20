"""A human can verify/dispute advice without recording fictional trades."""
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from tracker.models import Account, RebalanceExecution, StrategyInstance, StrategyPreset, StrategySignal


class AdvisoryFeedbackTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user("signal-owner", password="strong-test-password-123")
        self.other = User.objects.create_user("signal-other", password="strong-test-password-123")
        self.account = Account.objects.create(
            owner=self.owner, name="Owner ISA signal", kind="ISA",
            country="GB", broker="TRADING212",
        )
        self.strategy = StrategyInstance.objects.create(
            owner=self.owner, account=self.account,
            preset=StrategyPreset.objects.get(slug="nasdaq-quarterly-target"),
            name="9Sig Demo",
        )
        self.signal = StrategySignal.objects.create(
            strategy=self.strategy, signal_key="quarter:2026-Q3",
            kind="QUARTERLY", title="Review", as_of=timezone.localdate()-timedelta(days=15),
            message="Recommendation for review", actions=[{"side":"BUY", "quantity":"4"}],
        )
        self.url = reverse("v2_notice_feedback", args=[self.strategy.pk, self.signal.pk])

    def test_login_and_ownership_are_required(self):
        self.assertEqual(self.client.post(self.url, {"outcome": "CONFIRMED"}).status_code, 302)
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(self.url, {"outcome": "FALSE_POSITIVE", "review_note": "Wrong"}).status_code, 404)
        self.signal.refresh_from_db()
        self.assertEqual(self.signal.review_outcome, "")

    def test_false_positive_requires_note_and_does_not_touch_ledger(self):
        self.client.force_login(self.owner)
        self.client.post(self.url, {"outcome": "FALSE_POSITIVE", "review_note": ""})
        self.signal.refresh_from_db()
        self.assertEqual(self.signal.review_outcome, "")
        self.client.post(self.url, {"outcome": "FALSE_POSITIVE", "review_note": "  Workbook disagrees  "})
        self.signal.refresh_from_db()
        self.assertEqual(self.signal.review_outcome, "FALSE_POSITIVE")
        self.assertEqual(self.signal.review_note, "Workbook disagrees")
        self.assertIsNotNone(self.signal.acknowledged_at)
        self.assertEqual(self.signal.actions, [{"side":"BUY", "quantity":"4"}])
        self.assertEqual(self.strategy.ledger.count(), 0)

    def test_can_reclassify_an_error_after_rechecking(self):
        self.client.force_login(self.owner)
        self.client.post(self.url, {"outcome": "FALSE_POSITIVE", "review_note": "Initial concern"})
        self.client.post(self.url, {"outcome": "CONFIRMED", "review_note": "Verified with Excel"})
        self.signal.refresh_from_db()
        self.assertEqual(self.signal.review_outcome, "CONFIRMED")
        self.assertEqual(self.signal.review_note, "Verified with Excel")

    def test_completed_actual_review_cannot_be_relabelled_by_feedback(self):
        self.client.force_login(self.owner)
        RebalanceExecution.objects.create(
            signal=self.signal, strategy=self.strategy,
            executed_on=timezone.localdate(),
        )
        response = self.client.post(self.url, {
            "outcome": "FALSE_POSITIVE", "review_note": "Cannot rewrite execution",
        })
        self.assertEqual(response.status_code, 302)
        self.signal.refresh_from_db()
        self.assertEqual(self.signal.review_outcome, "")
        self.assertFalse(self.strategy.ledger.exists())
        self.assertEqual(self.client.get(self.url).status_code, 405)
