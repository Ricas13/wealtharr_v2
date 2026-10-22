"""Discord DMs: admin bot setup, Connect Discord (consent + server join), disconnect and sending."""
import io
import json
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from tracker.models import NotificationPreference, SystemIntegration
from tracker.services import dispatch, encrypt_secret


class _Reply(io.BytesIO):
    status = 200


def replies(*bodies):
    queue = [json.dumps(b).encode() for b in bodies]
    return lambda *args, **kwargs: _Reply(queue.pop(0))


def configure():
    cfg = SystemIntegration.current()
    cfg.discord_client_id = "1234567890"
    cfg.discord_guild_id = "5550001"
    cfg.discord_client_secret_ciphertext = encrypt_secret("client-secret-xyz")
    cfg.discord_bot_token_ciphertext = encrypt_secret("bot-token-xyz")
    cfg.save()
    return cfg


@override_settings(SITE_URL="http://testserver")
class ConnectDiscordTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("owner", "o@example.com", "a-long-test-password-1")
        self.client.force_login(self.user)

    def test_settings_explain_when_the_owner_has_not_set_up_dms(self):
        page = self.client.get(reverse("v2_settings"))
        self.assertContains(page, "not set up by the site owner yet")
        self.assertNotContains(page, "Connect Discord")
        self.assertRedirects(self.client.get(reverse("discord_connect")), reverse("v2_settings"))

    def test_connect_sends_to_discord_consent_with_state(self):
        configure()
        self.assertContains(self.client.get(reverse("v2_settings")), "Connect Discord")
        response = self.client.get(reverse("discord_connect"))
        url = urlsplit(response["Location"])
        query = parse_qs(url.query)
        self.assertEqual(url.netloc, "discord.com")
        self.assertEqual(query["scope"], ["identify guilds.join"])
        self.assertEqual(query["redirect_uri"], ["http://testserver/settings/discord/callback/"])
        self.assertEqual(query["state"], [self.client.session["discord_state"]])
        self.assertNotIn("client-secret-xyz", response["Location"])

    def test_callback_links_the_account_joins_the_server_and_turns_dms_on(self):
        configure()
        self.client.get(reverse("discord_connect"))
        state = self.client.session["discord_state"]
        calls = replies({"access_token": "user-access"}, {"id": "777888999", "global_name": "Ricas"}, {})
        with patch("tracker.discord_dm.urlopen", side_effect=calls) as http:
            response = self.client.get(reverse("discord_callback"), {"code": "abc", "state": state})
        self.assertRedirects(response, reverse("v2_settings"))
        token, me, join = [c.args[0] for c in http.call_args_list]
        self.assertTrue(token.full_url.endswith("/oauth2/token"))
        self.assertTrue(join.full_url.endswith("/guilds/5550001/members/777888999"))
        self.assertEqual(join.get_method(), "PUT")
        self.assertEqual(join.headers["Authorization"], "Bot bot-token-xyz")
        self.assertEqual(json.loads(join.data), {"access_token": "user-access"})
        pref = NotificationPreference.objects.get(owner=self.user)
        self.assertEqual((pref.discord_user_id, pref.discord_username, pref.discord_enabled),
                         ("777888999", "Ricas", True))
        page = self.client.get(reverse("v2_settings"))
        self.assertContains(page, "connected as Ricas")
        self.assertNotContains(page, "user-access")

    def test_callback_rejects_a_wrong_state_or_a_discord_failure(self):
        configure()
        self.client.get(reverse("discord_connect"))
        self.client.get(reverse("discord_callback"), {"code": "abc", "state": "forged"})
        self.assertFalse(NotificationPreference.objects.filter(owner=self.user).exclude(discord_user_id="").exists())
        self.client.get(reverse("discord_connect"))
        state = self.client.session["discord_state"]
        from urllib.error import HTTPError
        with patch("tracker.discord_dm.urlopen", side_effect=HTTPError("u", 400, "bad", {}, None)):
            response = self.client.get(reverse("discord_callback"), {"code": "abc", "state": state}, follow=True)
        self.assertContains(response, "Discord wasn&#x27;t connected")
        self.assertFalse(NotificationPreference.objects.filter(owner=self.user).exclude(discord_user_id="").exists())

    def test_disconnect_and_enabling_without_a_connection(self):
        configure()
        NotificationPreference.objects.create(owner=self.user, discord_user_id="1", discord_username="R",
                                              discord_enabled=True)
        self.client.post(reverse("discord_disconnect"))
        pref = NotificationPreference.objects.get(owner=self.user)
        self.assertEqual((pref.discord_user_id, pref.discord_enabled), ("", False))
        response = self.client.post(reverse("v2_settings"), {"form_kind": "owner", "discord_enabled": "on"})
        self.assertContains(response, "Connect your Discord account first")
        self.assertFalse(NotificationPreference.objects.get(owner=self.user).discord_enabled)


@override_settings(SITE_URL="http://testserver")
class DiscordAdminAndSendingTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("boss", "b@example.com", "a-long-test-password-1",
                                                         is_staff=True, is_superuser=True)
        self.client.force_login(self.user)

    def test_admin_saves_bot_settings_encrypted_and_shows_setup_steps(self):
        page = self.client.get(reverse("control"))
        self.assertContains(page, "http://testserver/settings/discord/callback/")
        self.assertContains(page, "Discord DMs not set up")
        self.client.post(reverse("control"), {
            "smtp_port": "587", "discord_client_id": "1234567890", "discord_guild_id": "5550001",
            "discord_client_secret": "client-secret-xyz", "discord_bot_token": "bot-token-xyz"})
        cfg = SystemIntegration.current()
        self.assertEqual((cfg.discord_client_id, cfg.discord_guild_id), ("1234567890", "5550001"))
        self.assertNotIn("bot-token-xyz", cfg.discord_bot_token_ciphertext)
        page = self.client.get(reverse("control"))
        self.assertContains(page, "Discord DMs set up")
        self.assertContains(page, "scope=bot")
        self.assertNotContains(page, "bot-token-xyz")
        self.assertNotContains(page, "client-secret-xyz")
        bad = self.client.post(reverse("control"), {"smtp_port": "587", "discord_guild_id": "my server"})
        self.assertContains(bad, "Discord IDs are numbers only")

    def test_an_unconnected_account_gets_no_dm(self):
        configure()
        NotificationPreference.objects.create(owner=self.user, discord_enabled=True)
        with patch("tracker.discord_dm.urlopen") as http:
            outcome = dispatch(self.user, "k1", "Subject", "Body", channels=("discord",))
        http.assert_not_called()
        self.assertEqual(outcome["discord"], "retrying")
