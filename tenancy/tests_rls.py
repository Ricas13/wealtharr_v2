"""Row-level security: the generated policy plan (any database) and the policies themselves (PostgreSQL)."""
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.db import DatabaseError, connection, transaction
from django.test import SimpleTestCase, TestCase, override_settings

from tenancy import rls
from tenancy.context import platform_context, tenant_context
from tracker.models import Account, LedgerEntry, StrategyInstance
from tracker.test_tenancy_core import _strategy

ROLE = "wealtharr_rls_test"


class PlanTests(SimpleTestCase):
    def test_every_model_with_an_owner_is_protected_and_the_rest_are_listed(self):
        protected, open_tables = rls.plan()
        names = {t for t, _ in protected}
        for model in rls.tracker_models():
            fields = {f.name for f in model._meta.concrete_fields}
            if fields & {"owner", "strategy", "account"}:
                self.assertIn(model._meta.db_table, names, model.__name__)
        self.assertIn("tracker_account", names)
        self.assertIn("tracker_ledgerentry", names)
        # Only shared market data and site-wide settings are left open.
        self.assertTrue({"tracker_studyprice", "tracker_systemintegration"} <= set(open_tables))

    @override_settings(RLS_ENABLED=False)
    def test_disabled_is_a_noop(self):
        self.assertIsNone(rls.push(5, bypass=False))


@skipUnless(connection.vendor == "postgresql", "row-level security needs PostgreSQL")
class PolicyTests(TestCase):
    def setUp(self):
        with connection.cursor() as c:
            c.execute("SELECT rolsuper OR rolcreaterole FROM pg_roles WHERE rolname = current_user")
            if not c.fetchone()[0]:
                self.skipTest("needs a database user that may create roles (CI uses a superuser)")
        User = get_user_model()
        self.alice = User.objects.create_user("alice", password="x" * 12)
        self.bob = User.objects.create_user("bob", password="x" * 12)
        self.a = _strategy(self.alice, "Alice strategy")
        self.b = _strategy(self.bob, "Bob strategy")
        with connection.cursor() as c:
            c.execute("SET CONSTRAINTS ALL IMMEDIATE")  # no pending FK checks when altering tables
            c.execute(f"DROP OWNED BY {ROLE}") if self._role_exists(c) else None
            c.execute(f"CREATE ROLE {ROLE} NOLOGIN") if not self._role_exists(c) else None
            for statement in rls.apply_statements(ROLE):
                c.execute(statement)
            c.execute(f"SET LOCAL ROLE {ROLE}")
        self.addCleanup(self._reset)

    @staticmethod
    def _role_exists(c):
        c.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [ROLE])
        return c.fetchone() is not None

    def _reset(self):
        with connection.cursor() as c:
            c.execute("RESET ROLE")

    def test_each_user_sees_only_their_rows_at_the_database(self):
        with override_settings(RLS_ENABLED=True):
            with tenant_context(self.alice):
                self.assertEqual(list(Account.objects.values_list("owner_id", flat=True)), [self.alice.pk])
                self.assertEqual(StrategyInstance.objects.count(), 1)
            with tenant_context(self.bob):
                self.assertEqual(list(Account.objects.values_list("owner_id", flat=True)), [self.bob.pk])

    def test_no_context_sees_nothing_and_platform_sees_all(self):
        with override_settings(RLS_ENABLED=True):
            self.assertEqual(Account.objects.count(), 0)  # fail closed
            with platform_context():
                self.assertEqual(Account.objects.count(), 2)

    def test_tables_reached_through_the_strategy_are_protected_too(self):
        from tracker.services import validate_and_record  # noqa: F401  (import check only)
        with platform_context(), override_settings(RLS_ENABLED=True):
            pass
        with override_settings(RLS_ENABLED=True):
            with tenant_context(self.alice):
                self.assertEqual(LedgerEntry.objects.filter(strategy=self.b).count(), 0)

    def test_cannot_write_another_users_row(self):
        with override_settings(RLS_ENABLED=True), tenant_context(self.alice):
            with self.assertRaises(DatabaseError), transaction.atomic():
                Account.objects.create(owner=self.bob, name="Planted", kind="ISA")

    def test_context_is_restored_after_a_block(self):
        with override_settings(RLS_ENABLED=True):
            with tenant_context(self.alice):
                with tenant_context(self.bob):
                    self.assertEqual(Account.objects.get().owner_id, self.bob.pk)
                self.assertEqual(Account.objects.get().owner_id, self.alice.pk)
            self.assertEqual(Account.objects.count(), 0)
