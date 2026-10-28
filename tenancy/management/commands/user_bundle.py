"""Back up or restore one user: `user_bundle export alice -o alice.json`, `user_bundle restore alice.json`."""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from tenancy import bundle


class Command(BaseCommand):
    help = "Export one user's records to a file, or restore such a file into a database that lacks them."

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest="action", required=True)
        out = sub.add_parser("export")
        out.add_argument("username")
        out.add_argument("-o", "--output", required=True)
        back = sub.add_parser("restore")
        back.add_argument("path")

    def handle(self, *args, **options):
        if options["action"] == "export":
            try:
                user = get_user_model().objects.get(username=options["username"])
            except get_user_model().DoesNotExist:
                raise CommandError("No such user.")
            with open(options["output"], "w", encoding="utf-8") as handle:
                handle.write(bundle.export_bundle(user))
            self.stdout.write("Wrote %s (treat it like a database backup)." % options["output"])
            return
        try:
            with open(options["path"], encoding="utf-8") as handle:
                count = bundle.restore_bundle(handle.read())
        except bundle.RestoreError as exc:
            raise CommandError(str(exc))
        self.stdout.write("Restored %d records." % count)
