"""Section H delivery integrity: owner opt-outs, frozen payloads, retries."""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from tracker.models import NotificationDelivery, NotificationPreference
from tracker.notification_routing import route_notice
from tracker.notification_scheduler import retry_due_deliveries
from tracker.services import dispatch


class AdvisoryDeliveryIntegrityTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(
            username="h-delivery-owner", email="owner@example.test",
            password="test-only-long-password")
        self.pref = NotificationPreference.objects.create(
            owner=self.owner, destination_email="owner@example.test",
            email_enabled=True, discord_enabled=False)

    def test_routed_notice_persists_original_category_and_sends_once(self):
        with patch("tracker.services._send_message") as send:
            first = route_notice(
                self.owner, "urgent", "signal:100:thirtydown",
                "30 Down advisory", "Investigate reference prices")
            second = route_notice(
                self.owner, "urgent", "signal:100:thirtydown",
                "Altered advice", "These are not new instructions")
        self.assertEqual(first, {"email": "sent"})
        self.assertEqual(second, {"email": "already_sent"})
        send.assert_called_once()
        record = NotificationDelivery.objects.get(owner=self.owner)
        self.assertEqual(record.category, "urgent")
        self.assertEqual(record.subject, "30 Down advisory")
        self.assertEqual(record.message, "Investigate reference prices")
        self.assertEqual(record.attempts, 1)

    def test_retry_uses_original_payload_not_newly_changed_advice(self):
        with patch("tracker.services._send_message",
                   side_effect=RuntimeError("transient destination failure")):
            result = dispatch(
                self.owner, "quarterly-digest:example", "Original quarter",
                "Recorded review at quarter close", channels=("email",),
                category="quarterly")
        self.assertEqual(result["email"], "retrying")
        row = NotificationDelivery.objects.get(owner=self.owner)
        self.assertEqual(row.attempts, 1)
        NotificationDelivery.objects.filter(pk=row.pk).update(
            next_retry_at=timezone.now() - timedelta(seconds=1))
        with patch("tracker.services._send_message") as send:
            result = dispatch(
                self.owner, "quarterly-digest:example", "Updated quarter",
                "Different proposed quantities", channels=("email",),
                category="quarterly")
        self.assertEqual(result["email"], "sent")
        self.assertEqual(send.call_args.args[-2:],
                         ("Original quarter", "Recorded review at quarter close"))
        row.refresh_from_db()
        self.assertEqual(row.message, "Recorded review at quarter close")
        self.assertEqual(row.attempts, 2)

    def test_existing_key_cannot_be_reclassified_as_other_alert_type(self):
        with patch("tracker.services._send_message") as send:
            self.assertEqual(dispatch(
                self.owner, "data:weekly:1", "Data issue", "Missing close",
                channels=("email",), category="data")["email"], "sent")
            self.assertEqual(dispatch(
                self.owner, "data:weekly:1", "Fake urgent", "Different body",
                channels=("email",), category="urgent")["email"], "failed")
        send.assert_called_once()
        self.assertEqual(NotificationDelivery.objects.get().category, "data")

    def test_owner_muting_cancels_queued_urgent_retry(self):
        row = NotificationDelivery.objects.create(
            owner=self.owner, channel="email",
            category="urgent", event_key="signal:200:up",
            subject="Urgent historical check", message="Review only",
            status="FAILED", attempts=1,
            next_retry_at=timezone.now() - timedelta(minutes=30))
        self.pref.email_enabled = False
        self.pref.save(update_fields=["email_enabled"])
        with patch("tracker.notification_scheduler.dispatch") as sender:
            self.assertEqual(retry_due_deliveries(), (0, 0))
        sender.assert_not_called()
        row.refresh_from_db()
        self.assertEqual(row.status, "CANCELLED")
        self.pref.email_enabled = True
        self.pref.save(update_fields=["email_enabled"])
        with patch("tracker.notification_scheduler.dispatch") as sender:
            self.assertEqual(retry_due_deliveries(), (0, 0))
        sender.assert_not_called()

    def test_legacy_retry_obeys_master_channel_switch(self):
        row = NotificationDelivery.objects.create(
            owner=self.owner, channel="email", event_key="old:untyped",
            subject="Unclassified old reminder", message="Historical text",
            status="FAILED", attempts=1)
        self.pref.email_enabled = False
        self.pref.save(update_fields=["email_enabled"])
        with patch("tracker.notification_scheduler.dispatch") as sender:
            self.assertEqual(retry_due_deliveries(), (0, 0))
        sender.assert_not_called()
        row.refresh_from_db()
        self.assertEqual(row.status, "CANCELLED")
