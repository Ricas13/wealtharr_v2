"""Install (or remove) PostgreSQL row-level security policies. Idempotent; run as the table owner.

Re-run it after every ``migrate`` that adds tables. See docs/saas/RLS.md.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from tenancy import rls


class Command(BaseCommand):
    help = "Install row-level security policies on tenant-owned tables (PostgreSQL only)."

    def add_arguments(self, parser):
        parser.add_argument("--role", default="", help="Database role the web app connects as (gets grants).")
        parser.add_argument("--force", action="store_true",
                            help="Also bind the table owner (single-role setups; run migrate as a superuser).")
        parser.add_argument("--remove", action="store_true", help="Drop the policies and disable RLS.")
        parser.add_argument("--dry-run", action="store_true", help="Print the SQL and change nothing.")

    def handle(self, *args, **options):
        if connection.vendor != "postgresql":
            raise CommandError("Row-level security needs PostgreSQL.")
        statements = rls.remove_statements() if options["remove"] else rls.apply_statements(options["role"], force=options["force"])
        if options["dry_run"]:
            self.stdout.write(";\n".join(statements) + ";")
            return
        with transaction.atomic(), connection.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)
        protected, open_tables = rls.plan()
        self.stdout.write("%s on %d tables." % ("Removed" if options["remove"] else "Policies installed", len(protected)))
        if open_tables:
            self.stdout.write("Not row-protected (no owner link): " + ", ".join(sorted(open_tables)))
