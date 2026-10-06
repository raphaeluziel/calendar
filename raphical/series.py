"""Saving and deleting events, including one or more dates of a repeating series.

Scopes, as offered when you change one date of a repeating event:
  'this'       only that date (stored as an exception event, or a skipped date)
  'following'  that date and every later one (the series is split in two)
  'all'        the whole series
"""

from django.db import transaction
from django.utils import timezone

from . import recurrence
from .models import Event, Reminder, occurrence_key, parse_occurrence_key

THIS, FOLLOWING, ALL = 'this', 'following', 'all'
SCOPES = (THIS, FOLLOWING, ALL)
FIELDS = ('title', 'start', 'end', 'all_day', 'color', 'description', 'rrule')


def resolve(event, occurrence_start=None):
    """(series, occurrence start, exception) for the date being edited.

    `event` is what the URL points at: a repeating event (with the chosen
    date in `occurrence_start`, default its first), an exception standing in
    for one date of a series, or a plain event (series is None).
    """
    if event.series_id:
        return event.series, event.original_start, event
    if event.is_repeating:
        return event, occurrence_start or event.start, None
    return None, None, None


def set_reminders(event, rows):
    """Make the event's reminders exactly `rows` [(method, minutes_before)]."""
    wanted = list(dict.fromkeys(rows))
    for reminder in event.reminders.all():
        key = (reminder.method, reminder.minutes_before)
        if key in wanted:
            wanted.remove(key)
            reminder.save()  # reschedule: the event's time may have changed
        else:
            reminder.delete()
    for method, minutes in wanted:
        Reminder.objects.create(event=event, method=method, minutes_before=minutes)


def reschedule(event):
    for reminder in event.reminders.all():
        reminder.save()


def _wall_delta(old, new):
    """Change in local clock time between two moments (ignores DST jumps)."""
    naive = lambda moment: timezone.localtime(moment).replace(tzinfo=None)
    return naive(new) - naive(old)


def _shift(moment, delta):
    return timezone.make_aware(timezone.localtime(moment).replace(tzinfo=None) + delta)


def _occurrences_before(series, moment):
    """How many dates the series' rule produces before `moment`."""
    count = 0
    for start in series.rule():
        if start >= moment:
            break
        count += 1
    return count


def _end_before(series, moment):
    """Cut `series` off just before `moment`, dropping changes to later dates."""
    parts = recurrence.parse(series.rrule)
    if 'COUNT' not in parts or _occurrences_before(series, moment) < int(parts['COUNT']):
        series.rrule = recurrence.truncated(series.rrule, moment)
    cutoff = occurrence_key(moment)
    series.exdates = [key for key in series.exdates if key < cutoff]
    series.exceptions.filter(original_start__gte=moment).delete()
    series.save()
    reschedule(series)


@transaction.atomic
def save_event(event, values, reminders, scope=ALL, occurrence_start=None):
    """Apply form `values` (dict of FIELDS) and `reminders` to `event`.

    Returns the event that now holds those values (which may be a new one:
    an exception for 'this', or the second half of a split for 'following').
    """
    series, start, exception = resolve(event, occurrence_start)
    if series is None:  # one-off event (possibly being made repeating)
        for field in FIELDS:
            setattr(event, field, values[field])
        event.save()
        set_reminders(event, reminders)
        return event

    series = Event.objects.get(pk=series.pk)  # the form may have changed the instance in memory

    if scope == THIS:
        target = exception or Event(owner=series.owner, series=series, original_start=start)
        for field in FIELDS:
            setattr(target, field, values[field])
        target.rrule = ''
        target.save()
        set_reminders(target, reminders)
        reschedule(series)  # that date no longer comes from the series
        return target

    if scope == FOLLOWING and start > series.start:
        parts = recurrence.parse(values['rrule'])
        if 'COUNT' in parts and parts['COUNT'] == recurrence.parse(series.rrule).get('COUNT'):
            # Same "ends after N times": the new half gets what was left.
            remaining = int(parts['COUNT']) - _occurrences_before(series, start)
            parts['COUNT'] = str(max(remaining, 1))
            values = {**values, 'rrule': recurrence.join(parts)}
        _end_before(series, start)
        new = Event(owner=series.owner, **{field: values[field] for field in FIELDS})
        new.save()
        set_reminders(new, reminders)
        return new

    # The whole series (or "following" from its first date, which is the same).
    delta = _wall_delta(start, values['start'])
    duration = values['end'] - values['start']
    series.start = _shift(series.start, delta)
    series.end = series.start + duration
    for field in ('title', 'all_day', 'color', 'description', 'rrule'):
        setattr(series, field, values[field])
    if not series.rrule:  # no longer repeating: drop per-date changes
        series.exdates = []
        series.exceptions.all().delete()
    elif delta:
        # Keep skipped and changed dates attached to the dates they belonged to.
        series.exdates = [
            occurrence_key(_shift(parse_occurrence_key(key), delta)) for key in series.exdates
        ]
        for changed in series.exceptions.all():
            changed.original_start = _shift(changed.original_start, delta)
            changed.save()
    series.save()
    set_reminders(series, reminders)
    if exception:  # editing a changed date "for all" folds it back into the series
        exception.delete()
    return series


@transaction.atomic
def delete_event(event, scope=ALL, occurrence_start=None):
    """Delete `event`, or the chosen dates of its series."""
    series, start, exception = resolve(event, occurrence_start)
    if series is None:
        event.delete()
        return
    series = Event.objects.get(pk=series.pk)
    if scope == THIS:
        if exception:
            exception.delete()
        series.exdates = sorted(set(series.exdates) | {occurrence_key(start)})
        series.save()
        reschedule(series)
    elif scope == FOLLOWING and start > series.start:
        _end_before(series, start)
    else:
        series.delete()
