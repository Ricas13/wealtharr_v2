"""Section H: routing choices, receipt reuse, scheduler health and owner isolation."""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from tracker.models import (
    NotificationDelivery, NotificationPreference, NotificationSchedulerRun,
)
from tracker.notification_routing import (
    newly_delivered, route_notice, selected_channels,
)
from tracker.notification_scheduler import run_notification_scheduler


class OwnerAlertMatrixTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(
            "alert_owner", email="owner@example.test",
            password="safe-test-password-for-alerts")
        self.other = get_user_model().objects.create_user(
            "alert_other", email="other@example.test",
            password="safe-test-password-for-alerts")
        self.pref = NotificationPreference.objects.create(
            owner=self.owner, destination_email="owner@example.test",
            email_enabled=True, discord_enabled=True)
        self.client.force_login(self.owner)

    def test_category_channel_matrix_selects_only_enabled_destinations(self):
        self.pref.urgent_discord = False
        self.pref.quarterly_email = False
        self.pref.save(update_fields=["urgent_discord", "quarterly_email"])
        self.assertEqual(selected_channels(self.pref, "urgent"), ("email",))
        self.assertEqual(selected_channels(self.pref, "quarterly"), ("discord",))
        self.assertEqual(selected_channels(self.pref, "data"), ("email", "discord"))
        self.pref.monthly_reminders = False
        self.pref.save(update_fields=["monthly_reminders"])
        self.assertEqual(selected_channels(self.pref, "contribution"), ())
        with self.assertRaises(ValueError):
            selected_channels(self.pref, "unknown")

    def test_muted_category_never_creates_delivery(self):
        self.pref.urgent_discord = False
        self.pref.urgent_email = False
        self.pref.save(update_fields=["urgent_email", "urgent_discord"])
        with patch("tracker.notification_routing.dispatch") as transport:
            response = route_notice(self.owner, "urgent", "spike:once",
                                    "Important source warning", "Review manually")
        self.assertEqual(response, {})
        transport.assert_not_called()
        self.assertFalse(NotificationDelivery.objects.exists())

    def test_already_delivered_event_not_reported_as_new_delivery(self):
        NotificationDelivery.objects.create(
            owner=self.owner, channel="email", event_key="quarterly-digest:1",
            subject="Old", message="Only the saved review", status="SENT",
            attempts=1)
        with patch("tracker.notification_routing.dispatch",
                   return_value={"email": "sent", "discord": "sent"}) as transport:
            outcomes = route_notice(self.owner, "quarterly",
                                    "quarterly-digest:1", "New", "Current")
        self.assertEqual(outcomes["email"], "already_sent")
        self.assertEqual(outcomes["discord"], "sent")
        self.assertEqual(newly_delivered(outcomes), 1)
        transport.assert_called_once()

    def test_owner_preference_post_updates_selected_categories_only(self):
        self.pref.quarterly_email = True
        self.pref.urgent_discord = True
        self.pref.save()
        params = {
            "save": "preferences", "notify-destination_email": "owner@example.test",
            "notify-email_enabled": "on",
            "notify-quarterly_discord": "on",
            "notify-urgent_email": "on",
            "notify-data_discord": "on",
            "notify-monthly_reminders": "on",
            "notify-followup_email": "on",
        }
        response = self.client.post(reverse("cockpit_settings"), params)
        self.assertRedirects(response, reverse("cockpit_settings"))
        self.pref.refresh_from_db()
        self.assertFalse(self.pref.discord_enabled)
        self.assertFalse(self.pref.quarterly_email)
        self.assertFalse(self.pref.urgent_discord)
        self.assertTrue(self.pref.urgent_email)
        self.assertTrue(self.pref.data_discord)

    def test_delivery_log_owner_scoped_and_scheduler_admin_only(self):
        NotificationDelivery.objects.create(
            owner=self.owner, channel="email", event_key="self",
            status="SENT", attempts=1)
        NotificationDelivery.objects.create(
            owner=self.other, channel="discord", event_key="private_other",
            status="FAILED", attempts=1)
        NotificationSchedulerRun.objects.create(
            name="owner_notices", status="SUCCEEDED", new_deliveries=2)
        response = self.client.get(reverse("cockpit_settings"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["recent_deliveries"]), 1)
        self.assertNotContains(response, "private_other")
        self.assertNotContains(response, "Scheduler health")
        self.owner.is_staff = True
        self.owner.save(update_fields=["is_staff"])
        response = self.client.get(reverse("cockpit_settings"))
        self.assertContains(response, "Scheduler health")
        self.assertContains(response, "2")

    def test_notification_settings_require_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("cockpit_settings")).status_code, 302)


class ScheduledOwnerNoticeTests(TestCase):
    def test_scheduler_success_and_command_alias_use_one_pipeline(self):
        with (patch("tracker.notification_scheduler.send_due_cockpit_notices",
                    return_value=2) as due,
              patch("tracker.notification_scheduler.retry_due_deliveries",
                    return_value=(3, 1)) as retry):
            run = run_notification_scheduler()
            self.assertEqual(run.status, "SUCCEEDED")
            self.assertEqual(run.new_deliveries, 2)
            self.assertEqual(run.attempted_retries, 3)
            self.assertEqual(run.successful_retries, 1)
            call_command("send_due_notifications", verbosity=0)
        self.assertEqual(NotificationSchedulerRun.objects.count(), 2)
        self.assertEqual(due.call_count, 2)
        self.assertEqual(retry.call_count, 2)

    def test_scheduler_failure_is_persisted_without_error_secret(self):
        with patch("tracker.notification_scheduler.send_due_cockpit_notices",
                   side_effect=RuntimeError("secret webhook / do not log")):
            with self.assertRaises(RuntimeError):
                run_notification_scheduler()
        run = NotificationSchedulerRun.objects.get()
        self.assertEqual(run.status, "FAILED")
        self.assertEqual(run.error_code, "SCHEDULED_DELIVERY_ERROR")
        self.assertNotIn("webhook", run.error_code)
        self.assertIsNotNone(run.finished_at)

    def test_retry_command_ignores_payloadless_legacy_delivery(self):
        user = get_user_model().objects.create_user(
            "payloadless", password="safe-test-password")
        NotificationPreference.objects.create(owner=user, email_enabled=True)
        NotificationDelivery.objects.create(
            owner=user, channel="email", event_key="old:payloadless",
            status="FAILED", attempts=1,
            next_retry_at=timezone.now() - timedelta(hours=1))
        with patch("tracker.notification_scheduler.dispatch") as send:
            call_command("retry_notifications", verbosity=0)
            send.assert_not_called()
