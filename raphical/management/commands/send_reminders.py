import time

from django.core.management.base import BaseCommand

from raphical.notifications import send_due_reminders

# How often to look for due reminders. Reminders are set in whole minutes,
# so this keeps them at most half a minute late.
CHECK_EVERY_SECONDS = 30


class Command(BaseCommand):
    help = 'Keep running and send event reminders as they come due. Ctrl+C to stop.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--once', action='store_true',
            help='Check one time and exit (for running from cron every minute).',
        )

    def handle(self, *args, once, **options):
        if once:
            self.stdout.write(f'Sent {send_due_reminders()} reminder(s).')
            return
        self.stdout.write('Sending reminders as they come due. Ctrl+C to stop.')
        try:
            while True:
                sent = send_due_reminders()
                if sent:
                    self.stdout.write(f'Sent {sent} reminder(s).')
                time.sleep(CHECK_EVERY_SECONDS)
        except KeyboardInterrupt:
            self.stdout.write('Stopped.')
