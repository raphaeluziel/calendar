from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from raphical import recurrence
from raphical.ics_import import apply_plan, plan_import, read_ics
from raphical.notifications import describe_when

CHOICES = {'1': 'existing', '2': 'imported', 'b': 'both', 'm': 'merge'}


class Command(BaseCommand):
    help = (
        'Import events from .ics files. When an event looks like one already on the '
        'calendar (similar title, same day) you are asked which to keep, or to merge them. '
        'Exact duplicates are skipped. Repeating events are imported as repeating events. '
        'Nothing is saved until all questions are answered.'
    )

    def add_arguments(self, parser):
        parser.add_argument('files', nargs='+', type=Path, help='.ics file(s) to import')
        parser.add_argument('--user', help='Username to import for (default: the only user).')
        parser.add_argument(
            '--on-duplicate', choices=['ask', *CHOICES.values()], default='ask',
            help='What to do with likely duplicates without asking (default: ask).',
        )
        parser.add_argument('--dry-run', action='store_true', help="Show what would happen; save nothing.")

    def handle(self, *args, files, user, on_duplicate, dry_run, **options):
        owner = self._user(user)

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

    def _user(self, username):
        users = get_user_model().objects.all()
        if username:
            try:
                return users.get(username=username)
            except users.model.DoesNotExist:
                raise CommandError(f'No user named {username!r}.')
        if users.count() != 1:
            raise CommandError('There is more than one user; say which with --user.')
        return users.get()

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
