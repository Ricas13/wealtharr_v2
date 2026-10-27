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
from tenancy.models import AuditChain, AuditEvent, AuditImmutable


class AuditBase(TestCase):
    def setUp(self):
        User = get_user_model()
        self.alice = User.objects.create_user("alice", password="correct-horse-battery-1")
        self.bob = User.objects.create_user("bob", password="correct-horse-battery-2")


class RecorderTests(AuditBase):
    def test_events_chain_per_user_and_platform(self):
        first = audit.record("org.test", actor=self.alice, subject=self.alice, metadata={"n": 1})
        second = audit.record("org.test", actor=self.alice, subject=self.alice, metadata={"n": 2})
        other = audit.record("org.test", actor=self.bob, subject=self.bob)
        platform = audit.record("platform.test")
        self.assertEqual(first.prev_hash, audit.GENESIS)
        self.assertEqual(second.prev_hash, first.hash)
        self.assertEqual(other.prev_hash, audit.GENESIS, "users have separate chains")
        self.assertEqual(platform.chain, "platform")
        self.assertEqual(AuditChain.objects.get(key="user:%d" % self.alice.pk).last_hash, second.hash)
        self.assertEqual(first.actor_label, "alice")

    def test_target_and_request_details_are_captured(self):
        self.client.force_login(self.alice)
        request = self.client.get(reverse("v2_overview"), HTTP_X_REQUEST_ID="req-123",
                                  HTTP_X_FORWARDED_FOR="203.0.113.9").wsgi_request
        with override_settings(TRUSTED_PROXY_HOPS=1):
            event = audit.record("account.viewed", request=request, target=self.alice)
        self.assertEqual((event.actor_id, event.subject_id), (self.alice.pk, self.alice.pk))
        self.assertEqual((event.target_type, event.target_id), ("User", str(self.alice.pk)))
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
        self.event = audit.record("org.test", subject=self.alice)

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

    def test_deleting_the_user_keeps_history(self):
        audit.record("auth.login", actor=self.bob, subject=self.bob)
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
            audit.record("org.test", subject=self.alice, metadata={"n": n})
        audit.record("platform.test")
        out = StringIO()
        call_command("verify_audit_chain", stdout=out)
        self.assertIn("4 events across 2 chains", out.getvalue())
        if connection.vendor == "postgresql":
            self.skipTest("on PostgreSQL the trigger stops tampering before it can be detected")
        second = AuditEvent.objects.filter(chain="user:%d" % self.alice.pk).order_by("id")[1]
        with connection.cursor() as cursor:
            cursor.execute("UPDATE tenancy_auditevent SET action = 'edited' WHERE id = %s", [second.pk])
        with self.assertRaises(CommandError) as raised:
            call_command("verify_audit_chain", stdout=StringIO())
        self.assertIn("contents do not match", str(raised.exception))

    def test_a_removed_event_is_found(self):
        if connection.vendor == "postgresql":
            self.skipTest("on PostgreSQL the trigger stops removal")
        for n in range(3):
            audit.record("org.test", subject=self.alice, metadata={"n": n})
        last = AuditEvent.objects.filter(chain="user:%d" % self.alice.pk).order_by("-id").first()
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
                                                 subject_id=self.alice.pk).exists())

    def test_login_success_is_recorded_for_the_user(self):
        from django.contrib.auth import login
        from django.test import RequestFactory
        from django.contrib.sessions.backends.db import SessionStore
        request = RequestFactory().get("/")
        request.session = SessionStore()
        login(request, self.alice, backend="django.contrib.auth.backends.ModelBackend")
        event = AuditEvent.objects.get(action="auth.login")
        self.assertEqual((event.actor_id, event.subject_id), (self.alice.pk, self.alice.pk))

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


class LegacyEventTests(AuditBase):
    def test_an_event_written_before_subjects_existed_still_verifies(self):
        """Events from the organization era hash exactly as they did, so history stays valid."""
        from django.utils import timezone
        chain_key = "org:7"
        event = AuditEvent(chain=chain_key, actor_id=self.alice.pk, actor_label="alice", organization_id=7,
                           action="auth.login", metadata={}, prev_hash=audit.GENESIS,
                           occurred_at=timezone.now())
        event.hash = audit.compute_hash(audit.GENESIS, event)
        event.save()
        AuditChain.objects.create(key=chain_key, last_hash=event.hash, last_event_id=event.pk)
        audit.record("auth.login", actor=self.alice)
        out = StringIO()
        call_command("verify_audit_chain", stdout=out)
        self.assertIn("2 events across 2 chains", out.getvalue())
