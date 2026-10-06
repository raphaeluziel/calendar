"""Write calendar events to one .ics file (the reverse of ics_import).

Repeating events are written as repeating events: their RRULE, skipped dates
(EXDATE) and changed dates (separate VEVENTs with RECURRENCE-ID). Times are
written in the calendar's time zone, with its VTIMEZONE, so other apps keep
repeating events at the right local time across daylight-saving changes.
Holidays aren't included; they're computed, not stored.
"""

import datetime

import icalendar
from django.utils import timezone

from .models import Event, Reminder

PRODID = "-//Raphi's Calendar//raphical//EN"
# Not a standard property; lets an export -> import round trip keep colors.
COLOR_PROPERTY = 'X-RAPHICAL-COLOR'


def _uid(event):
    return f'raphical-event-{event.pk}'


def _local(moment):
    return timezone.localtime(moment)


def _when(event, moment):
    """A start/end/recurrence-id value: a date for all-day events, else local time."""
    return _local(moment).date() if event.all_day else _local(moment)


def _alarm(reminder, email):
    alarm = icalendar.Alarm()
    alarm.add('TRIGGER', datetime.timedelta(minutes=-reminder.minutes_before))
    if reminder.method == Reminder.EMAIL and email:
        alarm.add('ACTION', 'EMAIL')
        alarm.add('SUMMARY', reminder.event.title)
        alarm.add('ATTENDEE', f'mailto:{email}')
    else:
        alarm.add('ACTION', 'DISPLAY')
    alarm.add('DESCRIPTION', reminder.event.title)
    return alarm


def _vevent(event, email, uid, stamp):
    vevent = icalendar.Event()
    vevent.add('UID', uid)
    vevent.add('DTSTAMP', stamp)
    vevent.add('CREATED', event.created)
    vevent.add('SUMMARY', event.title)
    vevent.add('DTSTART', _when(event, event.start))
    if event.all_day:
        # .ics end dates are exclusive; this calendar's all-day end is the last day.
        vevent.add('DTEND', _local(event.end).date() + datetime.timedelta(days=1))
    else:
        vevent.add('DTEND', _when(event, event.end))
    if event.description:
        vevent.add('DESCRIPTION', event.description)
    vevent.add(COLOR_PROPERTY, event.color)
    for reminder in event.reminders.all():
        vevent.add_component(_alarm(reminder, email))
    return vevent


def export_calendar(user):
    """The user's events as an icalendar.Calendar."""
    calendar = icalendar.Calendar()
    calendar.add('PRODID', PRODID)
    calendar.add('VERSION', '2.0')
    calendar.add('CALSCALE', 'GREGORIAN')
    calendar.add('X-WR-CALNAME', "Raphi's Calendar")
    calendar.add('X-WR-TIMEZONE', timezone.get_current_timezone_name())
    stamp = timezone.now()

    events = (Event.objects.filter(owner=user, series__isnull=True)
              .prefetch_related('reminders', 'exceptions__reminders').order_by('start'))
    for event in events:
        vevent = _vevent(event, user.email, _uid(event), stamp)
        if event.rrule:
            vevent.add('RRULE', icalendar.vRecur.from_ical(event.rrule))
            for key in event.exdates:
                skipped = datetime.datetime.strptime(key, '%Y%m%dT%H%M%SZ').replace(tzinfo=datetime.UTC)
                vevent.add('EXDATE', _when(event, skipped))
        calendar.add_component(vevent)

        # Dates changed on their own share the series' UID, marked by RECURRENCE-ID.
        for changed in event.exceptions.all():
            vchanged = _vevent(changed, user.email, _uid(event), stamp)
            vchanged.add('RECURRENCE-ID', _when(event, changed.original_start))
            calendar.add_component(vchanged)

    calendar.add_missing_timezones()
    return calendar
