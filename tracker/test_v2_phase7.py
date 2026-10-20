"""Phase 7: notification routing, Settings authorization and diagnostic honesty."""
from datetime import timedelta
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from tracker.models import (
    Account, NotificationDelivery, NotificationPreference,
    NotificationSchedulerRun, StrategyInstance, StrategyPreset,
    SystemIntegration,
)
from tracker.notification_routing import route_notice, strategy_channels
from tracker.notification_scheduler import retry_due_deliveries


class SettingsAndRoutingTests(TestCase):
    def setUp(self):
        users = get_user_model()
        self.owner = users.objects.create_user(
            username="settings-owner", password="safe-owner-pass",
            email="owner@example.test",
        )
        self.other = users.objects.create_user(
            username="other-owner", password="safe-other-pass",
            email="other@example.test",
        )
        preset, _ = StrategyPreset.objects.get_or_create(
            slug="nasdaq-quarterly-target",
            defaults={"name": "9Sig", "risk": "HIGH",
                      "summary": "Tracking only", "active": True},
        )
        a = Account.objects.create(
            owner=self.owner, name="Owner ISA", country="GB",
            kind="ISA", broker="TRADING212",
        )
        b = Account.objects.create(
            owner=self.other, name="Other ISA", country="GB",
            kind="ISA", broker="TRADING212",
        )
        self.own = StrategyInstance.objects.create(
            owner=self.owner, account=a, preset=preset,
            name="Private first portfolio", equity_symbol="LQQ3.L",
            safe_symbol="ERNS.L", strategy_key="nasdaq-quarterly-target",
            tracking_only=True, alert_routes={
                key: {"email": True, "discord": False}
                for key in ("quarterly", "urgent", "unacted", "data", "contribution")
            },
        )
        self.foreign = StrategyInstance.objects.create(
            owner=self.other, account=b, preset=preset,
            name="PRIVATE_OTHER_PERSON_SECRET",
            strategy_key="nasdaq-quarterly-target",
            tracking_only=True,
        )
        self.pref = NotificationPreference.objects.create(
            owner=self.owner, destination_email=self.owner.email,
            email_enabled=True, discord_enabled=True,
        )
        self.url = reverse("v2_settings")
        self.client.force_login(self.owner)

    def test_owner_settings_keeps_all_transport_secrets_private(self):
        cfg = SystemIntegration.current()
        cfg.smtp_host = "smtp.private.test"
        cfg.smtp_sender = "from@example.test"
        cfg.smtp_password_ciphertext = "SECRET_SMTP_CIPHERTEXT"
        cfg.discord_url_ciphertext = "SECRET_DISCORD_CIPHERTEXT"
        cfg.save()
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Notification destinations")
        self.assertContains(resp, "Private first portfolio")
        self.assertContains(resp, "Unverified")
        self.assertContains(resp, "Enable 2FA")
        self.assertNotContains(resp, "PRIVATE_OTHER_PERSON_SECRET")
        self.assertNotContains(resp, "SECRET_SMTP_CIPHERTEXT")
        self.assertNotContains(resp, "SECRET_DISCORD_CIPHERTEXT")
        self.assertNotContains(resp, "smtp.private.test")
        self.assertNotContains(resp, "Scheduler diagnostics" if False else "Never tested — OK")

    def test_global_preferences_and_delay_are_saved_and_validated(self):
        resp = self.client.post(self.url, {
            "form_kind": "owner",
            "destination_email": "newowner@example.test",
            "email_enabled": "on",
            "followup_delay_days": "11",
            "urgent_email": "on",
            "quarterly_email": "on",
        })
        self.assertEqual(resp.status_code, 302)
        self.pref.refresh_from_db()
        self.assertTrue(self.pref.email_enabled)
        self.assertFalse(self.pref.discord_enabled)
        self.assertEqual(self.pref.followup_delay_days, 11)
        self.assertEqual(self.pref.destination_email, "newowner@example.test")
        self.assertFalse(self.pref.data_email)
        invalid = self.client.post(self.url, {
            "form_kind": "owner", "email_enabled": "on",
            "destination_email": "newowner@example.test",
            "followup_delay_days": "0",
        })
        self.assertEqual(invalid.status_code, 200)
        self.assertContains(invalid, "1 to 30 whole days")
        self.pref.refresh_from_db()
        self.assertEqual(self.pref.followup_delay_days, 11)

    def test_per_strategy_routes_and_followup_alias(self):
        post = {
            "form_kind": "strategy", "strategy_id": str(self.own.pk),
            "route_quarterly_email": "on",
            "route_unacted_email": "on",
        }
        resp = self.client.post(self.url, post)
        self.assertEqual(resp.status_code, 302)
        self.own.refresh_from_db()
        self.assertFalse(self.own.alert_routes["urgent"]["email"])
        self.assertTrue(self.own.alert_routes["unacted"]["email"])
        self.assertEqual(
            strategy_channels(self.pref, "followup", self.own), ("email",)
        )
        self.assertEqual(strategy_channels(self.pref, "urgent", self.own), ())
        fake = Mock(return_value={"email": "sent"})
        self.assertEqual(route_notice(
            self.owner, "urgent", "signal:123:urgent", "Test", "Safe notice",
            sender=fake, strategy=self.own,
        ), {})
        fake.assert_not_called()
        self.assertEqual(route_notice(
            self.owner, "followup", "rebalance-reminder:123:1",
            "Test", "Safe notice", sender=fake, strategy=self.own,
        ), {"email": "sent"})
        self.assertEqual(fake.call_count, 1)
        self.assertEqual(fake.call_args.kwargs["channels"], ("email",))

    def test_foreign_or_invalid_instance_is_not_modified(self):
        for malicious in (str(self.foreign.pk), "not-a-number"):
            with self.subTest(id=malicious):
                response = self.client.post(self.url, {
                    "form_kind": "strategy", "strategy_id": malicious,
                    "route_urgent_email": "on",
                })
                self.assertEqual(response.status_code, 404)
        self.foreign.refresh_from_db()
        self.assertEqual(self.foreign.alert_routes, {})
        self.assertNotContains(self.client.get(self.url), "PRIVATE_OTHER_PERSON_SECRET")

    def test_unconfigured_v2_fails_closed_and_legacy_still_inherits(self):
        self.own.alert_routes = {}
        self.own.save(update_fields=["alert_routes"])
        self.assertEqual(strategy_channels(self.pref, "urgent", self.own), ())
        self.own.strategy_key = ""
        self.own.save(update_fields=["strategy_key"])
        self.assertEqual(strategy_channels(self.pref, "urgent", self.own),
                         ("email", "discord"))
        self.own.alert_routes = {"urgent": {"email": "yes", "discord": False}}
        self.own.save(update_fields=["alert_routes"])
        self.assertEqual(strategy_channels(self.pref, "urgent", self.own), ())

    def test_pending_scoped_retry_is_cancelled_after_strategy_opt_out(self):
        row = NotificationDelivery.objects.create(
            owner=self.owner, strategy=self.own, category="urgent",
            channel="email", event_key="signal:%s:test" % self.own.pk,
            subject="Safe subject", message="Safe body",
            status="FAILED", attempts=1,
        )
        self.own.alert_routes["urgent"]["email"] = False
        self.own.save(update_fields=["alert_routes"])
        with patch("tracker.notification_scheduler.dispatch") as sender:
            attempted, sent = retry_due_deliveries()
        self.assertEqual((attempted, sent), (0, 0))
        row.refresh_from_db()
        self.assertEqual(row.status, "CANCELLED")
        self.assertEqual(row.attempts, 1)
        sender.assert_not_called()
        # Re-enabling does not resurrect dated cancelled notifications.
        self.own.alert_routes["urgent"]["email"] = True
        self.own.save(update_fields=["alert_routes"])
        with patch("tracker.notification_scheduler.dispatch") as sender:
            retry_due_deliveries()
        row.refresh_from_db()
        self.assertEqual(row.status, "CANCELLED")
        sender.assert_not_called()

    def test_scoped_retry_remembers_original_strategy_provenance(self):
        row = NotificationDelivery.objects.create(
            owner=self.owner, strategy=self.own, category="data",
            channel="email", event_key="data:%s:2026-W43:abc" % self.own.pk,
            subject="Pending notice", message="Source issue",
            status="FAILED", attempts=1,
        )
        with patch("tracker.notification_scheduler.dispatch",
                   return_value={"email": "sent"}) as send:
            self.assertEqual(retry_due_deliveries(), (1, 1))
        self.assertEqual(send.call_args.kwargs["strategy"].pk, self.own.pk)
        self.assertEqual(send.call_args.kwargs["category"], "data")

    def test_receipts_and_global_diagnostics_do_not_cross_owner(self):
        NotificationDelivery.objects.create(
            owner=self.other, channel="email", category="urgent",
            event_key="other-only", subject="OTHER_SUBJECT_SECRET",
            message="OTHER_MESSAGE_SECRET", status="SENT",
        )
        NotificationSchedulerRun.objects.create(
            name="owner_notices", status="FAILED", error_code="DIAGNOSTIC_STAFF_ONLY",
            finished_at=timezone.now(),
        )
        resp = self.client.get(self.url)
        self.assertNotContains(resp, "OTHER_SUBJECT_SECRET")
        self.assertNotContains(resp, "OTHER_MESSAGE_SECRET")
        self.assertNotContains(resp, "DIAGNOSTIC_STAFF_ONLY")
        self.assertNotContains(resp, "Recent success")
        self.owner.is_staff = True
        self.owner.save(update_fields=["is_staff"])
        resp = self.client.get(self.url)
        self.assertContains(resp, "DIAGNOSTIC_STAFF_ONLY")
        self.assertContains(resp, "Not verified healthy")

    def test_send_test_staff_only_post_and_respects_master_switch(self):
        test_url = reverse("v2_notification_test", args=["email"])
        self.assertNotEqual(self.client.get(test_url).status_code, 200)
        self.assertEqual(self.client.post(test_url).status_code, 302)
        self.owner.is_staff = True
        self.owner.save(update_fields=["is_staff"])
        self.assertEqual(self.client.get(test_url).status_code, 405)
        with patch("tracker.v2_settings.dispatch", return_value={"email": "sent"}) as sender:
            resp = self.client.post(test_url)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(sender.call_count, 1)
        self.pref.email_enabled = False
        self.pref.save(update_fields=["email_enabled"])
        with patch("tracker.v2_settings.dispatch") as sender:
            self.client.post(test_url)
        sender.assert_not_called()

    def test_settings_requires_login_and_rejects_unknown_form(self):
        self.assertEqual(self.client.post(self.url, {"form_kind": "unknown"}).status_code, 200)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.assertEqual(self.client.post(self.url, {"form_kind": "owner"}).status_code, 302)
