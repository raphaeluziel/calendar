from pathlib import Path

from django.core.management.base import CommandError

from raphical import recurrence
from raphical.ics_import import apply_plan, plan_import, read_ics
from raphical.management.base import CalendarCommand
from raphical.notifications import describe_when

CHOICES = {'1': 'existing', '2': 'imported', 'b': 'both', 'm': 'merge'}


class Command(CalendarCommand):
    help = (
        'Import events from one or more .ics files (for example, exported from Google\n'
        'Calendar, Outlook or Apple Calendar), asking about likely duplicates.'
    )
    usage_text = """
duplicates:
  An imported event on the same day as an existing one, with a similar title
  (ignoring case and punctuation, or one title containing the other), is a
  likely duplicate. Both are shown and you choose:
    1  keep the existing event (the imported one is skipped)
    2  keep the imported event (it replaces the existing one)
    b  keep both
    m  merge: for each difference (title, time, repeat, description) choose
       which to keep; for the description you can also keep both. Reminders
       are combined.
  Events identical to one already on the calendar are skipped without asking,
  so importing the same file twice adds nothing.

what is imported:
  Title, times, all-day dates, description, "N minutes before" alarms (as
  reminders), and colors from files made by export_ics. Repeating events stay
  repeating, with their skipped and individually changed dates. Times with no
  time zone are taken as New York time.

Nothing is saved until every question is answered; Ctrl+C partway through
leaves the calendar unchanged.

examples:
  python manage.py import_ics google.ics
  python manage.py import_ics work.ics home.ics            several files at once
  python manage.py import_ics google.ics --dry-run         show what would happen, save nothing
  python manage.py import_ics google.ics --on-duplicate both   don't ask; keep both
"""

    def add_arguments(self, parser):
        parser.add_argument('files', nargs='+', type=Path, help='.ics file(s) to import.')
        parser.add_argument('--user', help='Username to import for (default: the only user).')
        parser.add_argument(
            '--on-duplicate', choices=['ask', *CHOICES.values()], default='ask',
            help='Answer every likely duplicate this way instead of asking (default: ask).',
        )
        parser.add_argument('--dry-run', action='store_true', help="Show what would happen; save nothing.")

    def handle(self, *args, files, user, on_duplicate, dry_run, **options):
        owner = self.get_owner(user)

        incoming = []
        for path in files:
            try:
                items = read_ics(path.read_bytes())
            except OSError as exc:
                raise CommandError(f"Can't read {path}: {exc.strerror}")
            except ValueError as exc:
                raise CommandError(f"{path} isn't a valid .ics file: {exc}")
            repeating = sum(1 for item in items if item.rrule)
            self.stdout.write(f'{path}: {len(items)} event(s)'
                              + (f', {repeating} of them repeating' if repeating else ''))
            incoming += items

        decide = self._ask if on_duplicate == 'ask' else (lambda old, new: on_duplicate)
        choose = self._ask_field if on_duplicate == 'ask' else self._auto_field
        try:
            plan = plan_import(owner, incoming, decide, choose)
        except (KeyboardInterrupt, EOFError):
            raise CommandError('Import cancelled; nothing was changed.')

        if not dry_run:
            apply_plan(owner, plan)
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('Dry run, nothing saved:' if dry_run else 'Import finished:'))
        for label, count in [
            ('added', plan.added),
            ('skipped (already on the calendar)', plan.identical),
            ('duplicates: kept existing', plan.kept_existing),
            ('duplicates: replaced with imported', plan.replaced),
            ('duplicates: kept both', plan.kept_both),
            ('duplicates: merged', plan.merged),
        ]:
            if count:
                self.stdout.write(f'  {count} {label}')

    # --- questions

    def _show(self, label, item):
        self.stdout.write(f'  [{label}] "{item.title}"  {describe_when(item)}')
        if item.rrule:
            self.stdout.write(f'      Repeats: {recurrence.describe(item.rrule, item.day)}')
        if item.description:
            text = ' '.join(item.description.split())
            self.stdout.write(f'      {text[:100]}{"…" if len(text) > 100 else ""}')

    def _prompt(self, question, valid):
        while True:
            answer = input(question).strip().lower()
            if answer in valid:
                return answer
            self.stdout.write(f'  Please type one of: {", ".join(valid)}')

    def _ask(self, existing, imported):
        self.stdout.write('')
        self.stdout.write(self.style.WARNING(
            f'Possible duplicate on {existing.day:%a, %b} {existing.day.day}, {existing.day.year}:'))
        self._show('1 existing' if existing.event else '1 earlier in this import', existing)
        self._show('2 imported', imported)
        answer = self._prompt(
            'Keep [1] existing, [2] imported, [b] both, or [m] merge? ', list(CHOICES))
        return CHOICES[answer]

    def _ask_field(self, field, existing, imported):
        if field == 'title':
            a, b = existing.title, imported.title
        elif field == 'time':
            a, b = describe_when(existing), describe_when(imported)
        elif field == 'repeat':
            a, b = (recurrence.describe(i.rrule, i.day) for i in (existing, imported))
        else:
            a, b = existing.description, imported.description
        self.stdout.write(f'  {field.capitalize()} differs:')
        self.stdout.write(f'    [1] {a}')
        self.stdout.write(f'    [2] {b}')
        if field == 'description':
            return int(self._prompt('  Use [1], [2], or [3] both? ', ['1', '2', '3']))
        return int(self._prompt('  Use [1] or [2]? ', ['1', '2']))

    @staticmethod
    def _auto_field(field, existing, imported):
        """--on-duplicate=merge: keep the existing values, but keep both descriptions."""
        return 3 if field == 'description' else 1
