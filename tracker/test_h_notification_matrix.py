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
            "form_kind": "owner", "followup_delay_days": "7",
            "destination_email": "owner@example.test", "email_enabled": "on",
            "quarterly_discord": "on", "urgent_email": "on",
            "data_discord": "on", "monthly_reminders": "on",
            "followup_email": "on",
        }
        response = self.client.post(reverse("v2_settings"), params)
        self.assertRedirects(response, reverse("v2_settings"))
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
        response = self.client.get(reverse("v2_settings"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["last_receipts"]), 1)
        self.assertNotContains(response, "private_other")
        self.assertNotContains(response, "Notification scheduler")
        self.owner.is_staff = True
        self.owner.save(update_fields=["is_staff"])
        response = self.client.get(reverse("v2_settings"))
        self.assertContains(response, "Notification scheduler")
        self.assertContains(response, "2")

    def test_turning_off_urgent_email_suspends_its_pending_retry(self):
        user = self.owner
        self.pref.urgent_email = False
        self.pref.save(update_fields=["urgent_email"])
        NotificationDelivery.objects.create(
            owner=user, channel="email", event_key="signal:my:spike",
            subject="Unverified reference", message="Do not trade",
            status="FAILED", attempts=1,
            next_retry_at=timezone.now() - timedelta(minutes=10))
        from tracker.notification_scheduler import retry_due_deliveries
        with patch("tracker.notification_scheduler.dispatch") as sender:
            self.assertEqual(retry_due_deliveries(), (0, 0))
            sender.assert_not_called()

    def test_cancelled_notice_never_resumes_when_owner_opts_back_in(self):
        from tracker.notification_scheduler import retry_due_deliveries
        delivery = NotificationDelivery.objects.create(
            owner=self.owner, channel="email", category="urgent",
            event_key="signal:old:spike", status="FAILED", attempts=1,
            subject="Old source warning", message="Already expired",
            next_retry_at=timezone.now() - timedelta(minutes=10))
        self.pref.urgent_email = False
        self.pref.urgent_discord = False
        self.pref.save(update_fields=["urgent_email", "urgent_discord"])
        with patch("tracker.notification_scheduler.dispatch") as sender:
            self.assertEqual(retry_due_deliveries(), (0, 0))
            sender.assert_not_called()
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, "CANCELLED")
        self.pref.urgent_email = True
        self.pref.save(update_fields=["urgent_email"])
        with patch("tracker.services._send_message") as sender:
            response = route_notice(
                self.owner, "urgent", "signal:old:spike",
                "New attempt", "Do not invent old data")
            sender.assert_not_called()
        self.assertEqual(response["email"], "cancelled")
        delivery.refresh_from_db()
        self.assertEqual(delivery.attempts, 1)

    def test_notification_settings_require_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("v2_settings")).status_code, 302)


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


class ScheduledDigestBehaviourTests(TestCase):
    def setUp(self):
        from tracker.models import Account, StrategyInstance, StrategyPreset, StrategySignal
        from tracker.market_signals import quarter_end_before
        self.owner = get_user_model().objects.create_user(
            "digest_owner", password="test-notification-password")
        NotificationPreference.objects.create(
            owner=self.owner, email_enabled=True, discord_enabled=False,
            monthly_reminders=False)
        account = Account.objects.create(
            owner=self.owner, name="My ISA", kind="ISA",
            country="GB", broker="TRADING212")
        preset, _ = StrategyPreset.objects.get_or_create(
            slug="nasdaq-quarterly-target",
            defaults={"name": "9Sig", "summary": "", "risk": "HIGH"})
        self.strategy = StrategyInstance.objects.create(
            owner=self.owner, account=account, preset=preset,
            name="My strategy", equity_symbol="LQQ3.L", safe_symbol="ERNS.L",
            market_data_enabled=True, monthly_target=0)
        self.today = timezone.localdate()
        self.signal = StrategySignal.objects.create(
            strategy=self.strategy, signal_key="quarter:test:alert",
            kind="QUARTERLY", as_of=quarter_end_before(self.today),
            title="Quarterly review", message="Review only", actions=[
                {"side": "BUY", "symbol": "ERNS.L", "quantity": "2",
                 "estimated_gbp": "100"},
            ])

    def test_one_quarterly_digest_no_second_send_on_repeated_tick(self):
        from tracker.cockpit_notifications import send_due_cockpit_notices
        with (patch("tracker.cockpit_notifications._price_status",
                    return_value=(True, "", [])),
              patch("tracker.cockpit_notifications.ledger_summary",
                    return_value={"positions": {}}),
              patch("tracker.services._send_message") as transport):
            self.assertEqual(send_due_cockpit_notices(today=self.today), 1)
            self.assertEqual(send_due_cockpit_notices(today=self.today), 0)
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(NotificationDelivery.objects.filter(
            owner=self.owner, status="SENT").count(), 1)

    def test_one_weekly_source_problem_no_executable_quarterly_notice(self):
        from tracker.cockpit_notifications import send_due_cockpit_notices
        with (patch("tracker.cockpit_notifications._price_status",
                    return_value=(False, "GBP quote on hold", [])),
              patch("tracker.cockpit_notifications.ledger_summary",
                    return_value={"positions": {"LQQ3.L": 1}}),
              patch("tracker.services._send_message") as transport):
            self.assertEqual(send_due_cockpit_notices(today=self.today), 1)
            self.assertEqual(send_due_cockpit_notices(today=self.today), 0)
        self.assertEqual(transport.call_count, 1)
        event = NotificationDelivery.objects.get(owner=self.owner)
        self.assertTrue(event.event_key.startswith("data:"))
        self.assertIn("Do not trade", event.message)

    def test_no_market_monitoring_still_reminds_about_real_contribution_plan(self):
        from decimal import Decimal as D
        from tracker.cockpit_notifications import send_due_cockpit_notices
        self.strategy.market_data_enabled = False
        self.strategy.monthly_target = D("500")
        self.strategy.save(update_fields=["market_data_enabled", "monthly_target"])
        pref = NotificationPreference.objects.get(owner=self.owner)
        pref.monthly_reminders = True
        pref.save(update_fields=["monthly_reminders"])
        target_day = self.today.replace(day=3)
        with patch("tracker.services._send_message") as transport:
            self.assertEqual(send_due_cockpit_notices(today=target_day), 1)
            self.assertEqual(send_due_cockpit_notices(today=target_day), 0)
        self.assertEqual(transport.call_count, 1)
        event = NotificationDelivery.objects.get(owner=self.owner)
        self.assertTrue(event.event_key.startswith("monthly:"))
        self.assertIn("not an automatic transfer", event.message)
