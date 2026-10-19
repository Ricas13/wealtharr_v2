"""Compatibility alias: no independent monthly notification stream."""
from django.core.management.base import BaseCommand
from django.core.management import call_command

class Command(BaseCommand):
    help = "Compatibility alias for canonical owner notice scheduler"
    def handle(self, *args, **kwargs):
        call_command("send_cockpit_notifications")
