from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from tracker.models import Account

from .models import AuditEvent, SupportGrant

User = get_user_model()


class SupportAccessTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("root", "r@x.test", "pw-Admin-1234")
        self.user = User.objects.create_user("pat", "p@x.test", "pw-User-1234")
        Account.objects.create(owner=self.user, name="SECRET ISA NAME", kind="ISA")
        self.users = self.client_class()
        self.users.force_login(self.user)
        self.client.force_login(self.admin)

    def allow(self, hours="4"):
        return self.users.post(reverse("support_access"), {"hours": hours, "reason": "help"})

    def test_no_consent_no_page(self):
        self.assertEqual(self.client.get(reverse("support_view", args=[self.user.pk])).status_code, 404)

    def test_consent_shows_metadata_only_and_logs_each_look(self):
        self.allow()
        resp = self.client.get(reverse("support_view", args=[self.user.pk]))
        self.assertContains(resp, "pat")
        self.assertNotContains(resp, "SECRET ISA NAME")
        self.client.get(reverse("support_view", args=[self.user.pk]))
        self.assertEqual(AuditEvent.objects.filter(action="support.viewed", subject_id=self.user.pk).count(), 2)
        log = self.users.get(reverse("support_access"))
        self.assertContains(log, "support.viewed")
        self.assertContains(log, "root")

    def test_expiry_and_revoke_end_access(self):
        self.allow()
        SupportGrant.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.client.get(reverse("support_view", args=[self.user.pk])).status_code, 404)
        self.allow()
        self.users.post(reverse("revoke_support_access"))
        self.assertEqual(self.client.get(reverse("support_view", args=[self.user.pk])).status_code, 404)

    def test_only_superuser_may_look_and_only_at_the_granting_user(self):
        self.allow()
        other = User.objects.create_user("eve", "e@x.test", "pw-User-1234")
        self.assertEqual(self.client.get(reverse("support_view", args=[other.pk])).status_code, 404)
        staff = User.objects.create_user("staff", "s@x.test", "pw-User-1234", is_staff=True)
        c = self.client_class()
        c.force_login(staff)
        self.assertEqual(c.get(reverse("support_view", args=[self.user.pk])).status_code, 302)
        self.assertEqual(self.users.get(reverse("support_view", args=[self.user.pk])).status_code, 302)

    def test_bad_duration_rejected_and_index_lists_only_active(self):
        self.users.post(reverse("support_access"), {"hours": "9999"})
        self.assertFalse(SupportGrant.objects.exists())
        self.allow()
        self.assertContains(self.client.get(reverse("support_index")), "pat")
