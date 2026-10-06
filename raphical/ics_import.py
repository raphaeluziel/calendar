"""Read .ics files into calendar events, spotting likely duplicates.

Used by the `import_ics` management command. Work happens in two steps so an
interrupted import changes nothing: `plan_import` decides what to do with every
event (asking about possible duplicates through callbacks), then `apply_plan`
writes it all to the database in one transaction.

Repeating events come in as repeating events: the file's RRULE is kept,
EXDATEs become skipped dates, and changed dates (RECURRENCE-ID) become
"this event only" changes. Extra one-off dates (RDATE), which this calendar
has no equivalent for, become separate events.
"""

import dataclasses
import datetime
import difflib
import re

import icalendar
from django.db import transaction
from django.utils import timezone

from . import recurrence
from .models import Event, Reminder, occurrence_key

MAX_REMINDERS = 5
SIMILAR_RATIO = 0.75


@dataclasses.dataclass(eq=False)
class Item:
    """An event being considered: either already saved (`event` set) or to be imported."""

    title: str
    start: datetime.datetime
    end: datetime.datetime
    all_day: bool
    description: str = ''
    reminders: list = dataclasses.field(default_factory=list)  # [(method, minutes_before)]
    color: str = Event.DEFAULT_COLOR
    rrule: str = ''
    exdates: list = dataclasses.field(default_factory=list)      # occurrence keys
    exceptions: list = dataclasses.field(default_factory=list)   # Items for changed dates
    original_start: datetime.datetime | None = None              # set on those Items
    event: Event | None = None
    delete: bool = False
    changed: bool = False

    @classmethod
    def from_event(cls, event):
        return cls(
            title=event.title, start=event.start, end=event.end, all_day=event.all_day,
            description=event.description, color=event.color, event=event,
            rrule=event.rrule, exdates=list(event.exdates),
            reminders=[(r.method, r.minutes_before) for r in event.reminders.all()],
        )

    @property
    def day(self):
        return timezone.localtime(self.start).date()

    # describe_when() and the prompts treat an Item like an Event.
    @property
    def is_repeating(self):
        return bool(self.rrule)

    def same_as(self, other):
        key = lambda i: (i.title, i.start, i.end, i.all_day, i.description.strip(),
                         recurrence.parse(i.rrule), sorted(i.exdates))
        return key(self) == key(other)


# --- reading .ics files

def _aware(value):
    """Floating times (no time zone in the file) are taken as local time."""
    return timezone.make_aware(value) if timezone.is_naive(value) else value


def _midnight(date):
    return timezone.make_aware(datetime.datetime.combine(date, datetime.time.min))


def _moment(value):
    """A DTSTART/EXDATE/RECURRENCE-ID value as an aware datetime (dates: local midnight)."""
    if isinstance(value, datetime.datetime):
        return _aware(value)
    return _midnight(value)


def _dates(component, name):
    """All values of a property that may repeat and hold lists (EXDATE, RDATE)."""
    found = component.get(name)
    if found is None:
        return []
    found = found if isinstance(found, list) else [found]
    return [d.dt for prop in found for d in prop.dts]


def _reminders(component):
    found = []
    for alarm in component.walk('VALARM'):
        trigger = alarm.get('TRIGGER')
        # Only "N minutes before the start" alarms map onto this calendar's reminders.
        if trigger is None or not isinstance(trigger.dt, datetime.timedelta):
            continue
        if trigger.params.get('RELATED', 'START') != 'START' or trigger.dt > datetime.timedelta(0):
            continue
        minutes = int(-trigger.dt.total_seconds() // 60)
        method = Reminder.EMAIL if str(alarm.get('ACTION', '')).upper() == 'EMAIL' else Reminder.PUSH
        if minutes <= Reminder.MAX_MINUTES_BEFORE and (method, minutes) not in found:
            found.append((method, minutes))
    return found[:MAX_REMINDERS]


def _item(component):
    start = component.decoded('DTSTART')
    if 'DTEND' in component:
        end = component.decoded('DTEND')
    elif 'DURATION' in component:
        end = start + component.decoded('DURATION')
    else:
        end = None

    if isinstance(start, datetime.datetime):
        start = _aware(start)
        end = _aware(end) if isinstance(end, datetime.datetime) else start
        all_day = False
    else:
        # All-day: the file's end date is exclusive; this calendar's is inclusive.
        last = (end - datetime.timedelta(days=1)) if end else start
        start, end, all_day = _midnight(start), _midnight(max(last, start)), True

    return Item(
        title=(str(component.get('SUMMARY', '')).strip() or '(No title)')[:200],
        start=start, end=max(end, start), all_day=all_day,
        description=str(component.get('DESCRIPTION', '')).strip(),
        reminders=_reminders(component),
    )


def _cancelled(component):
    return str(component.get('STATUS', '')).upper() == 'CANCELLED'


def read_ics(data):
    """The file's events as Items; repeating ones carry their rule and changed dates."""
    calendar = icalendar.Calendar.from_ical(data)
    components = calendar.walk('VEVENT')
    series = {}   # UID -> Item for repeating events
    items = []

    for component in components:
        if 'RECURRENCE-ID' in component or _cancelled(component):
            continue
        item = _item(component)
        if 'RRULE' in component:
            item.rrule = recurrence.normalized(component['RRULE'].to_ical().decode())
            item.exdates = sorted({occurrence_key(_moment(d)) for d in _dates(component, 'EXDATE')})
            series[str(component.get('UID'))] = item
        items.append(item)
        # Extra dates have no equivalent here; add each as its own event.
        for extra in _dates(component, 'RDATE'):
            if isinstance(extra, tuple):  # a PERIOD; use its start
                extra = extra[0]
            start = _moment(extra)
            items.append(dataclasses.replace(
                item, start=start, end=start + (item.end - item.start),
                rrule='', exdates=[], exceptions=[], reminders=list(item.reminders)))

    # Changed (or cancelled) dates of a series, listed as separate VEVENTs.
    for component in components:
        if 'RECURRENCE-ID' not in component:
            continue
        original = _moment(component.decoded('RECURRENCE-ID'))
        parent = series.get(str(component.get('UID')))
        if parent is None:  # the series isn't in this file; keep the date as a one-off
            if not _cancelled(component):
                items.append(_item(component))
            continue
        if _cancelled(component):
            parent.exdates = sorted(set(parent.exdates) | {occurrence_key(original)})
        else:
            changed = _item(component)
            changed.original_start = original
            parent.exceptions.append(changed)

    return sorted(items, key=lambda i: i.start)


# --- finding duplicates

def _words(title):
    return ' '.join(re.findall(r'[a-z0-9]+', title.casefold()))


def title_similarity(a, b):
    """1.0 for the same words; one title containing the other counts as very similar."""
    a, b = _words(a), _words(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if f' {a} ' in f' {b} ' or f' {b} ' in f' {a} ':
        return 0.9
    return difflib.SequenceMatcher(None, a, b).ratio()


def merge_values(existing, imported, choose):
    """Merge `imported` into `existing`, asking `choose(field, a, b)` about each difference.

    Fields are 'title', 'time', 'repeat' and 'description'. `choose` returns 1
    (existing's value), 2 (imported's) or, for the description only, 3 (both,
    one after the other). Reminders and skipped/changed dates are combined.
    """
    if existing.title != imported.title and choose('title', existing, imported) == 2:
        existing.title = imported.title
    times = lambda i: (i.start, i.end, i.all_day)
    if times(existing) != times(imported) and choose('time', existing, imported) == 2:
        existing.start, existing.end, existing.all_day = times(imported)
    if (recurrence.parse(existing.rrule) != recurrence.parse(imported.rrule)
            and choose('repeat', existing, imported) == 2):
        existing.rrule = imported.rrule
    a, b = existing.description.strip(), imported.description.strip()
    if a != b:
        pick = choose('description', existing, imported) if a and b else (1 if a else 2)
        existing.description = {1: a, 2: b, 3: f'{a}\n\n{b}'}[pick]
    for reminder in imported.reminders:
        if reminder not in existing.reminders and len(existing.reminders) < MAX_REMINDERS:
            existing.reminders.append(reminder)
    if existing.rrule:
        existing.exdates = sorted(set(existing.exdates) | set(imported.exdates))
        existing.exceptions += imported.exceptions
    existing.changed = True


@dataclasses.dataclass
class Plan:
    items: list          # everything on the calendar after the import
    added: int = 0
    identical: int = 0
    kept_existing: int = 0
    replaced: int = 0
    kept_both: int = 0
    merged: int = 0


def plan_import(user, incoming, decide, choose):
    """Decide what happens to each incoming Item.

    `decide(existing, imported)` is called for each likely duplicate (similar
    title, same first day) and returns 'existing', 'imported', 'both' or
    'merge'; merges ask `choose` (see `merge_values`). Incoming items are also
    compared with ones earlier in the same import.
    """
    plan = Plan(items=[Item.from_event(e) for e in
                       Event.objects.filter(owner=user).prefetch_related('reminders')])
    by_day = {}
    for item in plan.items:
        by_day.setdefault(item.day, []).append(item)

    for new in incoming:
        candidates = [
            (title_similarity(old.title, new.title), old)
            for old in by_day.get(new.day, []) if not old.delete
        ]
        candidates = [(score, old) for score, old in candidates if score >= SIMILAR_RATIO]
        if not candidates:
            plan.items.append(new)
            by_day.setdefault(new.day, []).append(new)
            plan.added += 1
            continue

        old = max(candidates, key=lambda c: c[0])[1]
        if old.same_as(new):
            plan.identical += 1
            continue

        choice = decide(old, new)
        if choice == 'existing':
            plan.kept_existing += 1
        elif choice == 'imported':
            old.delete = True
            plan.items.append(new)
            by_day[new.day].append(new)
            plan.replaced += 1
        elif choice == 'both':
            plan.items.append(new)
            by_day[new.day].append(new)
            plan.kept_both += 1
        elif choice == 'merge':
            merge_values(old, new, choose)
            plan.merged += 1
        else:
            raise ValueError(f'Unknown choice {choice!r}')
    return plan


def _save(user, item, series=None):
    event = item.event or Event(owner=user, color=item.color)
    for field in ('title', 'start', 'end', 'all_day', 'description', 'rrule'):
        setattr(event, field, getattr(item, field))
    event.exdates = item.exdates
    if series is not None:
        event.series, event.original_start, event.rrule = series, item.original_start, ''
    event.save()
    have = {(r.method, r.minutes_before) for r in event.reminders.all()}
    for method, minutes in item.reminders:
        if (method, minutes) not in have:
            Reminder.objects.create(event=event, method=method, minutes_before=minutes)
    return event


@transaction.atomic
def apply_plan(user, plan):
    for item in plan.items:
        if item.delete:
            if item.event:
                item.event.delete()
            continue
        if item.event and not item.changed:
            continue
        event = _save(user, item)
        if event.rrule:
            already = {occurrence_key(s) for s in
                       event.exceptions.values_list('original_start', flat=True)}
            for changed in item.exceptions:
                if occurrence_key(changed.original_start) not in already:
                    _save(user, changed, series=event)
        for reminder in event.reminders.all():
            reminder.save()  # schedule (and reschedule, if the time changed)
