"""The audit log: hash chains, append-only guarantees, sign-in events and verification."""
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, connection, transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from tenancy import audit
from tenancy.models import AuditChain, AuditEvent, AuditImmutable, Organization
from tenancy.services import personal_org_for


class AuditBase(TestCase):
    def setUp(self):
        User = get_user_model()
        self.alice = User.objects.create_user("alice", password="correct-horse-battery-1")
        self.bob = User.objects.create_user("bob", password="correct-horse-battery-2")
        self.a_org = personal_org_for(self.alice)
        self.b_org = personal_org_for(self.bob)


class RecorderTests(AuditBase):
    def test_events_chain_per_organization_and_platform(self):
        first = audit.record("org.test", actor=self.alice, organization=self.a_org, metadata={"n": 1})
        second = audit.record("org.test", actor=self.alice, organization=self.a_org, metadata={"n": 2})
        other = audit.record("org.test", actor=self.bob, organization=self.b_org)
        platform = audit.record("platform.test")
        self.assertEqual(first.prev_hash, audit.GENESIS)
        self.assertEqual(second.prev_hash, first.hash)
        self.assertEqual(other.prev_hash, audit.GENESIS, "organizations have separate chains")
        self.assertEqual(platform.chain, "platform")
        self.assertEqual(AuditChain.objects.get(key="org:%d" % self.a_org.pk).last_hash, second.hash)
        self.assertEqual(first.actor_label, "alice")

    def test_target_and_request_details_are_captured(self):
        self.client.force_login(self.alice)
        request = self.client.get(reverse("v2_overview"), HTTP_X_REQUEST_ID="req-123",
                                  HTTP_X_FORWARDED_FOR="203.0.113.9").wsgi_request
        with override_settings(TRUSTED_PROXY_HOPS=1):
            event = audit.record("account.viewed", request=request, target=self.a_org)
        self.assertEqual((event.actor_id, event.organization_id), (self.alice.pk, self.a_org.pk))
        self.assertEqual((event.target_type, event.target_id), ("Organization", str(self.a_org.pk)))
        self.assertEqual((event.request_id, event.ip), ("req-123", "203.0.113.9"))

    def test_metadata_must_be_plain_and_free_of_secrets(self):
        for bad in ({"password": "x"}, {"nested": {"api_token": "x"}}, {"webhook_url": "x"}, {"when": object()}):
            with self.assertRaises(ValueError):
                audit.record("bad", metadata=bad)
        self.assertEqual(AuditEvent.objects.count(), 0)

    def test_a_rolled_back_action_leaves_no_event(self):
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                audit.record("will.roll.back")
                raise RuntimeError("the action failed")
        self.assertEqual(AuditEvent.objects.count(), 0)


class AppendOnlyTests(AuditBase):
    def setUp(self):
        super().setUp()
        self.event = audit.record("org.test", organization=self.a_org)

    def test_orm_refuses_changes_and_removal(self):
        self.event.action = "edited"
        with self.assertRaises(AuditImmutable):
            self.event.save()
        with self.assertRaises(AuditImmutable):
            self.event.delete()
        with self.assertRaises(AuditImmutable):
            AuditEvent.objects.all().update(action="x")
        with self.assertRaises(AuditImmutable):
            AuditEvent.objects.all().delete()
        with self.assertRaises(AuditImmutable):
            AuditEvent.objects.bulk_create([AuditEvent()])

    def test_deleting_the_user_or_organization_keeps_history(self):
        audit.record("auth.login", actor=self.bob, organization=self.b_org)
        self.b_org.memberships.all().delete()
        Organization.objects.filter(pk=self.b_org.pk).update(personal_owner=None)
        self.b_org.delete()
        self.bob.delete()
        kept = AuditEvent.objects.get(action="auth.login")
        self.assertEqual(kept.actor_label, "bob")

    def test_database_refuses_raw_update_and_delete(self):
        if connection.vendor != "postgresql":
            self.skipTest("the trigger exists on PostgreSQL only")
        for sql in ("UPDATE tenancy_auditevent SET action = 'x'", "DELETE FROM tenancy_auditevent"):
            with self.assertRaises(DatabaseError):
                with transaction.atomic(), connection.cursor() as cursor:
                    cursor.execute(sql)


class VerifyTests(AuditBase):
    def test_clean_log_verifies_and_tampering_is_found(self):
        for n in range(3):
            audit.record("org.test", organization=self.a_org, metadata={"n": n})
        audit.record("platform.test")
        out = StringIO()
        call_command("verify_audit_chain", stdout=out)
        self.assertIn("4 events across 2 chains", out.getvalue())
        if connection.vendor == "postgresql":
            self.skipTest("on PostgreSQL the trigger stops tampering before it can be detected")
        second = AuditEvent.objects.filter(chain="org:%d" % self.a_org.pk).order_by("id")[1]
        with connection.cursor() as cursor:
            cursor.execute("UPDATE tenancy_auditevent SET action = 'edited' WHERE id = %s", [second.pk])
        with self.assertRaises(CommandError) as raised:
            call_command("verify_audit_chain", stdout=StringIO())
        self.assertIn("contents do not match", str(raised.exception))

    def test_a_removed_event_is_found(self):
        if connection.vendor == "postgresql":
            self.skipTest("on PostgreSQL the trigger stops removal")
        for n in range(3):
            audit.record("org.test", organization=self.a_org, metadata={"n": n})
        last = AuditEvent.objects.filter(chain="org:%d" % self.a_org.pk).order_by("-id").first()
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM tenancy_auditevent WHERE id = %s", [last.pk])
        with self.assertRaises(CommandError) as raised:
            call_command("verify_audit_chain", stdout=StringIO())
        self.assertIn("chain tip", str(raised.exception))


class SignInEventTests(AuditBase):
    def test_login_logout_and_failed_login_are_recorded_without_credentials(self):
        self.client.post(reverse("login"), {"username": "alice", "password": "wrong-password-xyz"})
        failed = AuditEvent.objects.get(action="auth.login_failed")
        self.assertIsNone(failed.actor_id)
        self.assertNotIn("alice", str(failed.metadata) + failed.actor_label)
        self.client.force_login(self.alice)
        self.client.post(reverse("logout"))
        self.assertTrue(AuditEvent.objects.filter(action="auth.logout", actor_id=self.alice.pk,
                                                 organization_id=self.a_org.pk).exists())

    def test_login_success_is_recorded_with_the_users_organization(self):
        from django.contrib.auth import login
        from django.test import RequestFactory
        from django.contrib.sessions.backends.db import SessionStore
        request = RequestFactory().get("/")
        request.session = SessionStore()
        login(request, self.alice, backend="django.contrib.auth.backends.ModelBackend")
        event = AuditEvent.objects.get(action="auth.login")
        self.assertEqual((event.actor_id, event.organization_id), (self.alice.pk, self.a_org.pk))

    def test_an_audit_failure_never_blocks_signing_in(self):
        from django.contrib.auth import login
        from django.test import RequestFactory
        from django.contrib.sessions.backends.db import SessionStore
        request = RequestFactory().get("/")
        request.session = SessionStore()
        with patch("tenancy.audit.record", side_effect=RuntimeError("audit store down")):
            with self.assertLogs("tenancy.signals", level="ERROR"):
                login(request, self.alice, backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(request.session["_auth_user_id"], str(self.alice.pk))
