"""Telegram alerts: admin bot token, person connects with a one-time code, delivery and routing."""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from tracker import telegram_bot
from tracker.models import NotificationPreference, SystemIntegration
from tracker.services import dispatch, encrypt_secret

TOKEN = "123456789:" + "A" * 35


def _bot(cfg):
    cfg.telegram_bot_token_ciphertext = encrypt_secret(TOKEN)
    cfg.telegram_bot_username = "WealtharrTestBot"
    cfg.save()


class TelegramAdminTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("tg-admin", password="a-long-test-password-1",
                                                         is_staff=True, is_superuser=True)
        self.client.force_login(self.user)

    def test_token_is_checked_and_stored_encrypted(self):
        with patch("tracker.telegram_bot._call", return_value={"username": "WealtharrTestBot"}):
            response = self.client.post(reverse("control"), {"form_kind": "telegram", "telegram-telegram_bot_token": TOKEN})
        self.assertRedirects(response, reverse("control") + "#telegram")
        cfg = SystemIntegration.current()
        self.assertEqual(cfg.telegram_bot_username, "WealtharrTestBot")
        self.assertNotIn(TOKEN, cfg.telegram_bot_token_ciphertext)
        page = self.client.get(reverse("control"))
        self.assertContains(page, "@WealtharrTestBot")
        self.assertNotContains(page, TOKEN)

    def test_a_rejected_token_saves_nothing_and_other_cards_are_untouched(self):
        cfg = SystemIntegration.current()
        cfg.smtp_host = "smtp.example.test"
        cfg.save()
        with patch("tracker.telegram_bot._call", side_effect=telegram_bot.TelegramError("Telegram refused the request.")):
            page = self.client.post(reverse("control"), {"form_kind": "telegram", "telegram-telegram_bot_token": TOKEN})
        self.assertContains(page, "Telegram didn&#x27;t accept this token")
        cfg.refresh_from_db()
        self.assertEqual((cfg.telegram_bot_username, cfg.smtp_host), ("", "smtp.example.test"))
        bad = self.client.post(reverse("control"), {"form_kind": "telegram", "telegram-telegram_bot_token": "nope"})
        self.assertContains(bad, "look like a bot token")


class TelegramConnectTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("tg-owner", password="a-long-test-password-1")
        self.client.force_login(self.user)
        _bot(SystemIntegration.current())

    def test_connect_with_a_one_time_code(self):
        page = self.client.get(reverse("v2_settings"))
        self.assertContains(page, "Connect Telegram")
        self.client.post(reverse("v2_settings"), {"form_kind": "telegram_start"})
        pref = NotificationPreference.objects.get(owner=self.user)
        code = pref.telegram_link_code
        self.assertTrue(telegram_bot.CODE.fullmatch(code))
        page = self.client.get(reverse("v2_settings"))
        self.assertContains(page, "https://t.me/WealtharrTestBot?start=" + code)
        updates = [{"message": {"text": "/start someone-elses-code-xyz", "chat": {"id": 1, "type": "private"}}},
                   {"message": {"text": "/start " + code, "chat": {"id": 4242, "type": "private"},
                                "from": {"username": "owner_tg"}}}]
        with patch("tracker.telegram_bot._call", return_value=updates):
            self.client.post(reverse("v2_settings"), {"form_kind": "telegram_finish"})
        pref.refresh_from_db()
        self.assertEqual((pref.telegram_chat_id, pref.telegram_username, pref.telegram_enabled, pref.telegram_link_code),
                         ("4242", "owner_tg", True, ""))
        self.assertContains(self.client.get(reverse("v2_settings")), "connected as owner_tg")
        self.client.post(reverse("v2_settings"), {"form_kind": "telegram_disconnect"})
        pref.refresh_from_db()
        self.assertEqual((pref.telegram_chat_id, pref.telegram_enabled), ("", False))

    def test_finish_without_pressing_start_or_after_expiry_connects_nothing(self):
        from datetime import timedelta
        from django.utils import timezone
        self.client.post(reverse("v2_settings"), {"form_kind": "telegram_start"})
        with patch("tracker.telegram_bot._call", return_value=[]):
            self.client.post(reverse("v2_settings"), {"form_kind": "telegram_finish"})
        pref = NotificationPreference.objects.get(owner=self.user)
        self.assertEqual(pref.telegram_chat_id, "")
        pref.telegram_link_expires = timezone.now() - timedelta(minutes=1)
        pref.save()
        with patch("tracker.telegram_bot._call") as call:
            self.client.post(reverse("v2_settings"), {"form_kind": "telegram_finish"})
        call.assert_not_called()

    def test_cannot_switch_on_without_connecting(self):
        response = self.client.post(reverse("v2_settings"), {"form_kind": "owner", "telegram_enabled": "on"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(NotificationPreference.objects.get(owner=self.user).telegram_enabled)


class TelegramDeliveryTests(TestCase):
    def test_dispatch_sends_to_the_connected_chat(self):
        user = get_user_model().objects.create_user("tg-send", password="a-long-test-password-1")
        _bot(SystemIntegration.current())
        NotificationPreference.objects.create(owner=user, telegram_enabled=True, telegram_chat_id="4242")
        with patch("tracker.telegram_bot._call", return_value={}) as call:
            outcome = dispatch(user, "event-tg", "Subject", "Body", channels=("telegram",))
        self.assertEqual(outcome, {"telegram": "sent"})
        token, method, payload = call.call_args[0]
        self.assertEqual((token, method, payload["chat_id"]), (TOKEN, "sendMessage", "4242"))
        self.assertIn("Body", payload["text"])

    def test_strategy_routes_have_a_telegram_column(self):
        from tracker.notification_routing import strategy_channels

        class Strategy:
            strategy_key = "all-weather"
            alert_routes = {"quarterly": {"email": False, "discord": False, "telegram": True}}
        user = get_user_model().objects.create_user("tg-routes", password="a-long-test-password-1")
        pref = NotificationPreference(owner=user, telegram_enabled=True, email_enabled=True)
        self.assertEqual(strategy_channels(pref, "quarterly", Strategy()), ("telegram",))
