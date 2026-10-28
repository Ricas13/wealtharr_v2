import json
import logging
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse

from tenancy import bundle, ops
from tenancy.models import Plan
from tracker.models import Account, LedgerEntry, StrategyInstance
from tracker.test_tenancy_core import _strategy

User = get_user_model()


class ReadinessTests(TestCase):
    def test_ready_when_database_is_migrated_and_has_a_default_plan(self):
        r = self.client.get(reverse("ready"))
        self.assertEqual((r.status_code, r.json()["status"]), (200, "ready"))
        self.assertTrue(all(c["ok"] for c in r.json()["checks"].values()))

    def test_not_ready_without_a_default_plan(self):
        Plan.objects.filter(is_default=True).update(is_default=False)
        r = self.client.get(reverse("ready"))
        self.assertEqual(r.status_code, 503)
        self.assertFalse(r.json()["checks"]["default_plan"]["ok"])

    def test_health_still_answers(self):
        self.assertEqual(self.client.get(reverse("health")).status_code, 200)


class RequestIdAndLogTests(TestCase):
    def test_request_id_is_kept_or_made_and_echoed(self):
        self.assertEqual(self.client.get("/health/", HTTP_X_REQUEST_ID="abc-123")["X-Request-ID"], "abc-123")
        self.assertEqual(len(self.client.get("/health/")["X-Request-ID"]), 16)

    def test_json_formatter_emits_one_object_with_the_request_id(self):
        record = logging.LogRecord("x", logging.WARNING, "f", 1, "hello %s", ("world",), None)
        record.request_id = "r1"
        line = json.loads(ops.JsonFormatter().format(record))
        self.assertEqual((line["message"], line["level"], line["request_id"]), ("hello world", "WARNING", "r1"))


class BundleTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user("alice", password="x" * 12)
        self.bob = User.objects.create_user("bob", password="x" * 12)
        self.a = _strategy(self.alice, "Alice strategy")
        _strategy(self.bob, "Bob strategy")

    def wipe(self, user):
        for obj in reversed(bundle.collect(user)):
            if not isinstance(obj, User):
                type(obj)._base_manager.filter(pk=obj.pk).delete()
        user.delete()

    def test_export_holds_only_that_user_and_round_trips(self):
        text = bundle.export_bundle(self.alice)
        self.assertNotIn("Bob strategy", text)
        before = (Account.objects.filter(owner=self.alice).count(),
                  StrategyInstance.objects.filter(owner=self.alice).count())
        self.wipe(self.alice)
        self.assertFalse(User.objects.filter(username="alice").exists())
        self.assertGreater(bundle.restore_bundle(text), 2)
        alice = User.objects.get(username="alice")
        self.assertEqual((Account.objects.filter(owner=alice).count(),
                          StrategyInstance.objects.filter(owner=alice).count()), before)
        self.assertEqual(StrategyInstance.objects.filter(owner=self.bob).count(), 1)

    def test_restore_refuses_to_overwrite(self):
        text = bundle.export_bundle(self.alice)
        with self.assertRaises(bundle.RestoreError):
            bundle.restore_bundle(text)

    def test_restore_is_all_or_nothing_on_a_bad_bundle(self):
        with self.assertRaises(bundle.RestoreError):
            bundle.restore_bundle('{"wealtharr_bundle": 99, "objects": []}')

    def test_commands(self):
        import os, tempfile
        path = os.path.join(tempfile.mkdtemp(), "a.json")
        call_command("user_bundle", "export", "alice", "-o", path, stdout=StringIO())
        with self.assertRaises(CommandError):
            call_command("user_bundle", "restore", path, stdout=StringIO())
        with self.assertRaises(CommandError):
            call_command("user_bundle", "export", "nobody", "-o", path, stdout=StringIO())
