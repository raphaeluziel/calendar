import time

from raphical.management.base import CalendarCommand
from raphical.notifications import send_due_reminders

# How often to look for due reminders. Reminders are set in whole minutes,
# so this keeps them at most half a minute late.
CHECK_EVERY_SECONDS = 30


class Command(CalendarCommand):
    help = (
        'Send the notification and email reminders set on events as each one comes due.\n'
        'Keeps running, checking every 30 seconds, until you press Ctrl+C.'
    )
    usage_text = """
During development you don't need to run this yourself: `python manage.py
runserver` starts it automatically (see manage.py). On a server, run it as its
own long-running service, or once a minute from cron with --once.

Reminders more than 15 minutes overdue (for example, from a time this wasn't
running) are skipped rather than sent late. Email reminders are printed in the
terminal until a mail server is configured in config/settings.py.

examples:
  python manage.py send_reminders          keep running, sending reminders as they come due
  python manage.py send_reminders --once   send whatever is due now, then exit

  crontab line for a server (runs every minute):
  * * * * * cd /path/to/calendar && venv/bin/python manage.py send_reminders --once
"""

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
