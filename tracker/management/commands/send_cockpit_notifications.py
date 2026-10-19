from django.core.management.base import BaseCommand
from django.core.management import call_command
from tracker.cockpit_notifications import send_due_cockpit_notices

class Command(BaseCommand):
    help = 'Send the concise 9Sig quarter-end digest, delayed reminders and monthly target'
    def handle(self, *args, **kwargs):
        count = send_due_cockpit_notices()
        self.stdout.write('Cockpit notifications delivered: %s' % count)
        call_command('retry_notifications')
