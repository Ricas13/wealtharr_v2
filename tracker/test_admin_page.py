"""Owner admin: staff-only links at the bottom of the normal sidebar and the admin page."""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from tracker.models import InviteRequest, NotificationPreference, SystemIntegration, YahooSync


class AdminPageTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("owner", password="a-long-test-password-1")
        self.client.force_login(self.user)

    def make_staff(self):
        self.user.is_staff = self.user.is_superuser = True
        self.user.save()

    def test_regular_users_see_no_admin_links_or_page(self):
        page = self.client.get(reverse("v2_overview"))
        self.assertNotContains(page, 'aria-label="Admin"')
        self.assertNotContains(page, reverse("control"))
        self.assertEqual(self.client.get(reverse("control")).status_code, 302)

    def test_staff_keep_the_normal_menu_with_admin_links_at_the_bottom(self):
        self.make_staff()
        page = self.client.get(reverse("v2_overview")).content.decode()
        for link in (reverse("v2_overview"), reverse("v2_add_strategy"), reverse("v2_settings"),
                     reverse("control"), reverse("admin:tracker_inviterequest_changelist"),
                     reverse("admin:auth_user_changelist"), reverse("admin:index")):
            self.assertIn('href="%s"' % link, page)
        # Admin links come after the personal menu, just above Collapse / Sign out.
        self.assertLess(page.index(reverse("v2_settings")), page.index('aria-label="Admin"'))
        self.assertLess(page.index('aria-label="Admin"'), page.index("w2-sidebar-footer"))

    def test_admin_page_saves_mail_settings_and_lists_price_problems(self):
        self.make_staff()
        InviteRequest.objects.create(email="friend@example.com")
        YahooSync.objects.create(symbol="SGLN.L", last_error="PRICE_HOLD:LARGE_MOVE")
        YahooSync.objects.create(symbol="VUAG.L", last_error="")
        page = self.client.get(reverse("control"))
        self.assertContains(page, "Mail server and Discord")
        self.assertContains(page, "PRICE_HOLD:LARGE_MOVE")
        self.assertNotContains(page, "VUAG.L")
        self.assertContains(page, "1 waiting to be read")
        self.assertContains(page, 'aria-current="page"')
        response = self.client.post(reverse("control"), {
            "smtp_host": "smtp.example.com", "smtp_port": "587", "smtp_username": "me",
            "smtp_sender": "me@example.com", "smtp_use_tls": "on"})
        self.assertRedirects(response, reverse("control"))
        self.assertEqual(SystemIntegration.current().smtp_host, "smtp.example.com")

    def test_channel_test_returns_to_the_admin_page(self):
        self.make_staff()
        NotificationPreference.objects.create(owner=self.user, email_enabled=True,
                                              destination_email="o@example.invalid")
        with patch("tracker.v2_settings.dispatch", return_value={"email": "sent"}):
            response = self.client.post(reverse("v2_notification_test", args=["email"]))
        self.assertRedirects(response, reverse("control"))
