import json
import re

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from tracker.models import Account, NotificationPreference, SystemIntegration

from . import accounts
from .models import AuditEvent

User = get_user_model()
STRONG = "Sturdy-Pass-91xq"


def smtp_on():
    cfg = SystemIntegration.current()
    cfg.smtp_host, cfg.smtp_sender = "smtp.invalid", "noreply@example.test"
    cfg.save()


@override_settings(SELF_SERVE_SIGNUP=True, EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class SignupTests(TestCase):
    def setUp(self):
        smtp_on()
        self.sent = []
        accounts.send_system_email = lambda to, subject, body: self.sent.append((to, body)) or True
        self.addCleanup(lambda: setattr(accounts, "send_system_email", __import__("tenancy.mail", fromlist=["x"]).send_system_email))

    def sign_up(self, **over):
        data = {"username": "newbie", "email": "new@example.test", "password1": STRONG, "password2": STRONG}
        data.update(over)
        return self.client.post(reverse("signup"), data)

    def test_signup_creates_inactive_user_and_emails_link(self):
        self.assertContains(self.sign_up(), "Check your email")
        user = User.objects.get(username="newbie")
        self.assertFalse(user.is_active)
        link = re.search(r"/signup/verify/\S+/", self.sent[0][1]).group(0)
        self.assertFalse(self.client.login(username="newbie", password=STRONG))
        self.assertContains(self.client.get(link), "Activate my account")
        user.refresh_from_db()
        self.assertFalse(user.is_active)  # a plain GET (mail scanner) activates nothing
        self.assertEqual(self.client.post(link).status_code, 302)
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertTrue(AuditEvent.objects.filter(action="account.email_verified").exists())

    def test_existing_email_looks_identical_and_creates_nothing(self):
        User.objects.create_user("owner", "new@example.test", STRONG)
        self.assertContains(self.sign_up(), "Check your email")
        self.assertFalse(User.objects.filter(username="newbie").exists())
        self.assertEqual(self.sent, [])

    def test_tampered_token_rejected(self):
        self.sign_up()
        link = re.search(r"/signup/verify/\S+/", self.sent[0][1]).group(0)
        self.assertEqual(self.client.post(link[:-3] + "x/").status_code, 400)

    def test_deactivated_veteran_cannot_reactivate_with_old_link(self):
        self.sign_up()
        link = re.search(r"/signup/verify/\S+/", self.sent[0][1]).group(0)
        User.objects.filter(username="newbie").update(is_active=False, last_login="2026-01-01T00:00:00Z")
        self.assertEqual(self.client.post(link).status_code, 400)

    def test_weak_password_rejected(self):
        self.sign_up(password1="123", password2="123")
        self.assertFalse(User.objects.filter(username="newbie").exists())

    def test_rate_limited(self):
        for i in range(accounts.SIGNUP_LIMIT):
            self.sign_up(username=f"u{i}", email=f"u{i}@example.test")
        self.assertContains(self.sign_up(username="late", email="late@example.test"), "Too many")

    @override_settings(SELF_SERVE_SIGNUP=False)
    def test_off_by_default_is_404(self):
        self.assertEqual(self.client.get(reverse("signup")).status_code, 404)
        self.assertEqual(self.client.post(reverse("signup"), {}).status_code, 404)


class PasswordResetTests(TestCase):
    def setUp(self):
        smtp_on()
        self.user = User.objects.create_user("pat", "pat@example.test", STRONG)
        self.sent = []
        import tenancy.accounts as a
        self._orig = a.send_system_email
        a.send_system_email = lambda to, subject, body: self.sent.append((to, body)) or True
        self.addCleanup(lambda: setattr(a, "send_system_email", self._orig))

    def test_reset_flow_and_same_answer_for_unknown_address(self):
        r1 = self.client.post(reverse("password_reset"), {"email": "nobody@example.test"})
        r2 = self.client.post(reverse("password_reset"), {"email": "pat@example.test"})
        self.assertEqual((r1.status_code, r1.url), (r2.status_code, r2.url))
        self.assertEqual(len(self.sent), 1)
        path = re.search(r"http://[^/]+(/password-reset/\S+/)", self.sent[0][1]).group(1)
        page = self.client.get(path, follow=True)
        post = page.request["PATH_INFO"]
        self.client.post(post, {"new_password1": "Another-Pass-77zz", "new_password2": "Another-Pass-77zz"})
        self.assertTrue(self.client.login(username="pat", password="Another-Pass-77zz"))

    def test_inactive_user_gets_no_email(self):
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        self.client.post(reverse("password_reset"), {"email": "pat@example.test"})
        self.assertEqual(self.sent, [])


class SessionsExportDeleteTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("sam", "sam@example.test", STRONG)
        self.client.login(username="sam", password=STRONG)

    def test_sessions_listed_and_others_revoked(self):
        other = self.client_class()
        other.login(username="sam", password=STRONG)
        other.get(reverse("account"))
        self.assertContains(self.client.get(reverse("account")), "Sign out everywhere else")
        self.client.post(reverse("revoke_sessions"), {"session": "others"})
        self.assertEqual(other.get(reverse("account")).status_code, 302)  # now signed out
        self.assertEqual(self.client.get(reverse("account")).status_code, 200)

    def test_cannot_revoke_someone_elses_session(self):
        eve = User.objects.create_user("eve", "e@example.test", STRONG)
        theirs = self.client_class()
        theirs.login(username="eve", password=STRONG)
        key = theirs.session.session_key
        self.client.post(reverse("revoke_sessions"), {"session": key})
        self.assertEqual(theirs.get(reverse("account")).status_code, 200)

    def test_export_has_own_data_only_and_no_secrets(self):
        acct = Account.objects.create(owner=self.user, name="My ISA", kind="ISA")
        Account.objects.create(owner=User.objects.create_user("eve", "e@x.test", STRONG), name="Eve ISA", kind="ISA")
        NotificationPreference.objects.create(owner=self.user, discord_webhook_ciphertext="SECRETBLOB",
                                              destination_email="sam@example.test")
        resp = self.client.post(reverse("account_export"))
        body = resp.content.decode()
        data = json.loads(body)
        self.assertEqual([r["name"] for r in data["records"]["Account"]], ["My ISA"])
        self.assertNotIn("SECRETBLOB", body)
        self.assertNotIn("Eve ISA", body)
        self.assertIn("attachment", resp["Content-Disposition"])
        self.assertEqual(self.client.get(reverse("account_export")).status_code, 405)

    def test_delete_requires_password_and_scrubs(self):
        NotificationPreference.objects.create(owner=self.user, destination_email="sam@example.test",
                                              email_enabled=True, telegram_chat_id="123")
        self.client.post(reverse("delete_account"), {"confirm": "DELETE", "password": "wrong"})
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)
        self.client.post(reverse("delete_account"), {"confirm": "DELETE", "password": STRONG})
        self.user.refresh_from_db()
        pref = NotificationPreference.objects.get(owner=self.user)
        self.assertFalse(self.user.is_active)
        self.assertEqual((self.user.email, self.user.username), ("", f"deleted-{self.user.pk}"))
        self.assertFalse(self.user.has_usable_password())
        self.assertEqual((pref.destination_email, pref.telegram_chat_id, pref.email_enabled), ("", "", False))
        self.assertEqual(self.client.get(reverse("account")).status_code, 302)
        self.assertTrue(AuditEvent.objects.filter(action="account.deleted", subject_id=self.user.pk).exists())

    def test_staff_cannot_self_delete(self):
        User.objects.filter(pk=self.user.pk).update(is_staff=True)
        self.client.post(reverse("delete_account"), {"confirm": "DELETE", "password": STRONG})
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)
