"""Phase 7 safety: fixed Strategy, settings, per-instance delivery and heartbeat."""
from datetime import timedelta
from decimal import Decimal as D
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from tracker.models import (
    Account, LedgerEntry, NotificationDelivery, NotificationPreference,
    NotificationSchedulerRun, StrategyInstance, StrategyPreset,
    SystemIntegration,
)
from tracker.notification_routing import (
    route_notice, instance_category_enabled,
)
from tracker.notification_scheduler import (
    _ping_dead_mans_switch, retry_due_deliveries,
)
from tracker.v2_phase7 import _heartbeat
from tracker.services import validate_and_record


class Phase7Tests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(
            username="phase7-owner", password="strong-test-password", email="owner@example.invalid"
        )
        self.other = User.objects.create_user(
            username="phase7-other", password="other-test-password", email="other@example.invalid"
        )
        self.preset, _ = StrategyPreset.objects.get_or_create(
            slug="nasdaq-quarterly-target",
            defaults={"name": "9Sig", "risk": "HIGH", "summary": "Source rules", "active": True},
        )
        self.strategy = self._make(self.owner, "Owner ISA")
        self.stranger = self._make(self.other, "SECRET_OTHER_ACCOUNT")
        self.pref = NotificationPreference.objects.create(
            owner=self.owner, destination_email="owner@example.invalid",
            email_enabled=True, discord_enabled=True,
        )
        self.other_pref = NotificationPreference.objects.create(
            owner=self.other, email_enabled=True, discord_enabled=True,
        )
        self.client.force_login(self.owner)

    def _make(self, owner, name):
        account = Account.objects.create(
            owner=owner, name=name, kind="ISA", country="GB", broker="TRADING212"
        )
        return StrategyInstance.objects.create(
            owner=owner, account=account, preset=self.preset, name=name,
            strategy_key="nasdaq-quarterly-target", platform_code="TRADING212",
            account_type_code="ISA", tracking_only=True,
            strategy_version="9sig-v1", mapping_version="2026-10-19.1",
            equity_symbol="LQQ3.L", safe_symbol="ERNS.L",
            alert_routes={
                "quarterly": {"email": True, "discord": False},
                "unacted": {"email": False, "discord": False},
                "urgent": {"email": True, "discord": False},
                "contribution": {"email": False, "discord": False},
                "data": {"email": True, "discord": True},
            },
        )

    def _url(self, instance=None):
        return reverse("v2_instance_page", kwargs={
            "instance_id": (instance or self.strategy).pk, "section": "strategy",
        })

    def test_strategy_rules_are_read_only_and_only_for_owner(self):
        response = self.client.get(self._url())
        self.assertContains(response, "How this strategy works")
        self.assertContains(response, "9% each completed quarter")
        self.assertContains(response, "Account check pending")
        self.assertContains(response, "ERNS")
        self.assertContains(response, "Tracking only")
        self.assertNotContains(response, "SECRET_OTHER_ACCOUNT")
        self.assertEqual(self.client.get(self._url(self.stranger)).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(self._url()).status_code, 302)

    def test_route_matrix_changes_only_this_instance_not_version_or_other(self):
        old_version = self.strategy.strategy_version
        self.client.post(self._url(), {
            "intent": "alerts", "quarterly_discord": "on",
            "data_email": "on", "unacted_email": "on",
            "strategy_key": "all-weather", "account_type_code": "SIPP",
            "tracking_only": "false",
        })
        self.strategy.refresh_from_db()
        self.stranger.refresh_from_db()
        self.assertTrue(self.strategy.alert_routes["unacted"]["email"])
        self.assertTrue(self.strategy.alert_routes["quarterly"]["discord"])
        self.assertFalse(self.strategy.alert_routes["quarterly"]["email"])
        self.assertFalse(self.strategy.alert_routes["urgent"]["email"])
        self.assertEqual(self.strategy.strategy_version, old_version)
        self.assertEqual(self.strategy.account.kind, "ISA")
        self.assertTrue(self.strategy.tracking_only)
        self.assertTrue(self.stranger.alert_routes["quarterly"]["email"])
        self.assertEqual(self.client.post(self._url(self.stranger), {
            "intent": "alerts", "quarterly_email": "on",
        }).status_code, 404)

    def test_disabled_instance_channel_cannot_dispatch(self):
        with patch("tracker.services._send_message") as send:
            result = route_notice(self.owner, "quarterly", "phase7-q-01",
                                  "Review", "No broker orders",
                                  strategy=self.strategy)
        self.assertIn(result.get("email"), ("sent", "retrying", "failed"))
        self.assertNotIn("discord", result)
        self.assertEqual(NotificationDelivery.objects.filter(
            owner=self.owner, strategy=self.strategy, channel="discord",
        ).count(), 0)
        self.assertEqual(NotificationDelivery.objects.filter(
            owner=self.owner, strategy=self.strategy, channel="email",
        ).count(), 1)
        self.assertEqual(NotificationDelivery.objects.filter(
            owner=self.other).count(), 0)

    def test_legacy_is_unmodified_by_instance_matrix(self):
        legacy_account = Account.objects.create(
            owner=self.owner, name="Legacy", kind="ISA",
            country="GB", broker="TRADING212")
        legacy = StrategyInstance.objects.create(
            owner=self.owner, account=legacy_account, preset=self.preset,
            name="Legacy", strategy_key="", alert_routes={})
        self.assertTrue(instance_category_enabled(legacy, "quarterly", "email"))
        self.assertTrue(instance_category_enabled(legacy, "quarterly", "discord"))

    def test_optout_cancels_retry_after_pending_delivery(self):
        row = NotificationDelivery.objects.create(
            owner=self.owner, strategy=self.strategy, channel="email",
            category="quarterly", event_key="quarterly-digest:%s:one" % self.strategy.pk,
            status="FAILED", attempts=1,
            subject="Review", message="Only actual fills",
        )
        self.strategy.alert_routes["quarterly"]["email"] = False
        self.strategy.save(update_fields=["alert_routes"])
        with patch("tracker.notification_scheduler.dispatch") as send:
            attempted, sent = retry_due_deliveries()
        row.refresh_from_db()
        self.assertEqual(row.status, "CANCELLED")
        self.assertEqual((attempted, sent), (0, 0))
        send.assert_not_called()

    def test_wrong_strategy_owner_is_denied_at_routing(self):
        with patch("tracker.services._send_message") as send:
            self.assertEqual(route_notice(
                self.owner, "quarterly", "mismatched-owner",
                "X", "Y", strategy=self.stranger,
            ), {})
        send.assert_not_called()

    def test_account_preferences_days_and_isa_toggle(self):
        response = self.client.get(reverse("v2_settings"))
        self.assertContains(response, "Notification destinations")
        self.assertContains(response, "Account security")
        self.assertContains(response, "Export my data")
        self.assertNotContains(response, "SECRET_OTHER_ACCOUNT")
        data = {
            "intent": "preferences", "destination_email": "new@example.invalid",
            "email_enabled": "on", "discord_enabled": "on",
            "quarterly_email": "on", "quarterly_discord": "on",
            "urgent_email": "on", "data_email": "on",
            "followup_email": "on", "contribution_email": "on",
            "monthly_reminders": "on", "unacted_reminder_days": "9",
            # absence of ISA checkbox disables advisory display.
        }
        self.assertRedirects(self.client.post(reverse("v2_settings"), data),
                             reverse("v2_settings"))
        self.pref.refresh_from_db()
        self.assertEqual(self.pref.unacted_reminder_days, 9)
        self.assertFalse(self.pref.isa_allowance_enabled)
        self.assertEqual(self.pref.destination_email, "new@example.invalid")
        self.assertFalse(self.other_pref.isa_allowance_enabled is False)

    def test_invalid_reminder_days_refuses_save(self):
        for number in ("0", "31", "-2", "xyz"):
            with self.subTest(number=number):
                resp = self.client.post(reverse("v2_settings"), {
                    "intent": "preferences", "unacted_reminder_days": number,
                })
                self.assertEqual(resp.status_code, 200)
                self.assertContains(resp, "errorlist")
        self.pref.refresh_from_db()
        self.assertEqual(self.pref.unacted_reminder_days, 3)

    def test_transport_and_test_actions_staff_only_and_secrets_hidden(self):
        self.assertEqual(self.client.post(reverse(
            "v2_test_channel", args=["discord"]
        )).status_code, 404)
        self.assertEqual(self.client.post(reverse("v2_settings"), {
            "intent": "transport",
            "transport-discord_webhook": "https://discord.com/api/webhooks/123/secret",
        }).status_code, 404)
        self.owner.is_staff = True
        self.owner.save(update_fields=["is_staff"])
        self.client.force_login(self.owner)
        response = self.client.post(reverse("v2_settings"), {
            "intent": "transport",
            "smtp_host": "mail.example.invalid", "smtp_port": "587",
            "smtp_sender": "sender@example.invalid",
            "smtp_use_tls": "on",
            "smtp_password": "top-secret-transport-password",
            "discord_webhook": "https://discord.com/api/webhooks/123/SafeToken12",
        })
        self.assertRedirects(response, reverse("v2_settings"))
        config = SystemIntegration.current()
        self.assertNotEqual(config.smtp_password_ciphertext, "top-secret-transport-password")
        self.assertNotEqual(config.discord_url_ciphertext, "https://discord.com/api/webhooks/123/SafeToken12")
        html = self.client.get(reverse("v2_settings")).content.decode("utf-8")
        self.assertNotIn("top-secret-transport-password", html)
        self.assertNotIn("SafeToken12", html)
        with patch("tracker.v2_phase7.dispatch", return_value={"discord": "sent"}) as send:
            self.assertRedirects(self.client.post(
                reverse("v2_test_channel", args=["discord"])
            ), reverse("v2_settings"))
        send.assert_called_once()

    def test_export_requires_password_and_does_not_include_secrets(self):
        response = self.client.post(reverse("v2_export_data"), {"password": "wrong"})
        self.assertRedirects(response, reverse("v2_settings"))
        response = self.client.post(reverse("v2_export_data"), {
            "password": "strong-test-password",
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn("application/zip", response["Content-Type"])
        import io, zipfile
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        for filename in ("strategies.csv", "ledger.csv", "revisions.csv",
                         "opening_holdings.csv", "reconciliations.csv",
                         "actual_fills.csv", "executions.csv", "broker_imports.csv",
                         "owner_wealth_snapshots.csv", "strategy_alert_routes.csv",
                         "alert_deliveries.csv"):
            self.assertIn(filename, archive.namelist())
        self.assertIn("Owner ISA", archive.read("strategies.csv").decode())
        self.assertNotIn("SECRET_OTHER_ACCOUNT", archive.read("strategies.csv").decode())
        self.assertNotIn("strong-test-password", response.content.decode("latin1", "ignore"))
        self.client.logout()
        self.assertEqual(self.client.post(reverse("v2_export_data"), {
            "password": "strong-test-password",
        }).status_code, 302)

    def test_reset_needs_typed_confirmation_password_and_forbids_financial_audit(self):
        url = reverse("v2_reset_empty")
        wrong = self.client.post(url, {
            "password": "strong-test-password", "confirmation": "oops",
        })
        self.assertRedirects(wrong, reverse("v2_settings"))
        self.assertTrue(StrategyInstance.objects.filter(pk=self.strategy.pk).exists())
        validate_and_record(self.strategy, LedgerEntry(
            kind="DEPOSIT", occurred_on=timezone.localdate()-timedelta(days=1),
            amount=D("250"),
        ))
        blocked = self.client.post(url, {
            "password": "strong-test-password",
            "confirmation": "ERASE MY EMPTY STRATEGIES",
        })
        self.assertRedirects(blocked, reverse("v2_settings"))
        self.assertTrue(StrategyInstance.objects.filter(pk=self.strategy.pk).exists())
        self.assertEqual(LedgerEntry.objects.filter(strategy=self.strategy).count(), 1)

    def test_reset_only_empty_owner_strategies(self):
        self.assertRedirects(self.client.post(reverse("v2_reset_empty"), {
            "password": "strong-test-password",
            "confirmation": "ERASE MY EMPTY STRATEGIES",
        }), reverse("v2_settings"))
        self.assertFalse(StrategyInstance.objects.filter(pk=self.strategy.pk).exists())
        self.assertTrue(StrategyInstance.objects.filter(pk=self.stranger.pk).exists())

    def test_scheduler_heartbeat_public_no_secrets(self):
        admin = reverse("v2_settings")
        self.owner.is_staff = True
        self.owner.save(update_fields=["is_staff"])
        self.client.force_login(self.owner)
        health = self.client.get(reverse("health"))
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["scheduler"]["state"], "never_run")
        before = timezone.now()
        row = NotificationSchedulerRun.objects.create(
            name="owner_notices", status="SUCCEEDED", finished_at=before,
        )
        health = self.client.get(reverse("health"))
        self.assertEqual(health.json()["scheduler"]["state"], "healthy")
        self.assertIn("last_success", health.json()["scheduler"])
        html = self.client.get(admin)
        self.assertContains(html, "Recent successful run")
        self.assertNotContains(html, "sqlite://")
        row.status = "FAILED"
        row.save(update_fields=["status"])
        health = self.client.get(reverse("health"))
        self.assertEqual(health.json()["scheduler"]["state"], "failed")

    def test_ping_not_sent_for_missing_or_unsafe_url(self):
        with patch.dict("os.environ", {"WEALTHARR_SCHEDULER_PING_URL": "http://127.0.0.1/private"}):
            with patch("urllib.request.urlopen") as mock_open:
                self.assertFalse(_ping_dead_mans_switch())
                mock_open.assert_not_called()
        with patch.dict("os.environ", {"WEALTHARR_SCHEDULER_PING_URL": "https://example.com/health"}):
            from unittest.mock import MagicMock
            ctx = MagicMock()
            ctx.__enter__.return_value.status = 200
            with patch("urllib.request.urlopen", return_value=ctx) as mock_open:
                self.assertTrue(_ping_dead_mans_switch())
                mock_open.assert_called_once()
