import sys
from pathlib import Path

from django.core.management.base import CommandError

from planner.ics_export import export_calendar
from planner.management.base import CalendarCommand


class Command(CalendarCommand):
    help = (
        'Export every event on the calendar to one .ics file, as a backup or to load\n'
        'into another calendar app.'
    )
    usage_text = """
The file has each event's title, times, description, color and reminders.
Repeating events are written as repeating events, with their skipped and
individually changed dates. Times are in New York time with the time zone
included, so other apps keep repeating events at the right local time.
Holidays are left out, since they're computed rather than stored.

Importing the file with import_ics recreates the events exactly; importing it
back into the same calendar adds nothing.

examples:
  python manage.py export_ics                      write calendar.ics
  python manage.py export_ics backup.ics           choose the file name
  python manage.py export_ics backup.ics --force   overwrite an existing file
  python manage.py export_ics - > backup.ics       print it instead of saving
"""

    def add_arguments(self, parser):
        parser.add_argument('file', nargs='?', default='calendar.ics',
                            help='Where to write it (default: calendar.ics); "-" prints it.')
        parser.add_argument('--user', help='Username to export (default: the only user).')
        parser.add_argument('--force', action='store_true', help='Overwrite the file if it exists.')

    def handle(self, *args, file, user, force, **options):
        owner = self.get_owner(user)
        data = export_calendar(owner).to_ical()
        if file == '-':
            sys.stdout.buffer.write(data)
            return
        path = Path(file)
        if path.exists() and not force:
            raise CommandError(f'{path} already exists; use --force to overwrite it.')
        try:
            path.write_bytes(data)
        except OSError as exc:
            raise CommandError(f"Can't write {path}: {exc.strerror}")
        count = owner.events.filter(series__isnull=True).count()
        self.stdout.write(self.style.SUCCESS(f'Exported {count} event(s) to {path}'))
