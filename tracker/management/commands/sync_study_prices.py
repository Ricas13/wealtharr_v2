"""Download long daily histories for the hypothetical Study tab, at most once a day."""
from django.core.management.base import BaseCommand

from tracker.study_data import sync_study_prices


class Command(BaseCommand):
    help = "Top up Study tab price histories (QQQ, SPY, VTI, GBP/USD, LQQ3, ERNS)"

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="Download even if fetched today")

    def handle(self, *args, **options):
        # Never fail the scheduler loop: a study download is optional.
        try:
            results = sync_study_prices(force=options["force"])
        except Exception as exc:
            self.stdout.write("Study prices: skipped (%s)" % type(exc).__name__)
            return
        self.stdout.write("Study prices: " + ", ".join("%s %s" % kv for kv in results.items()))
