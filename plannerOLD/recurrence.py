"""Repeat rules: the event form's choices <-> iCalendar RRULE strings.

Rules are stored as RRULE values (RFC 5545), e.g. "FREQ=MONTHLY;BYDAY=3TU",
the same format .ics files use.
"""

import datetime
import itertools

from dateutil.rrule import rrulestr
from django.utils import timezone
from django.utils.dateformat import format as date_format

WEEKDAY_CODES = ['MO', 'TU', 'WE', 'TH', 'FR', 'SA', 'SU']  # date.weekday() order
WEEKDAY_NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
ORDINALS = {1: 'first', 2: 'second', 3: 'third', 4: 'fourth', -1: 'last'}
FREQ_UNITS = {'DAILY': 'day', 'WEEKLY': 'week', 'MONTHLY': 'month', 'YEARLY': 'year'}
WEEKDAYS_ONLY = 'MO,TU,WE,TH,FR'

# Parts the Custom builder can show; rules using anything else (e.g. from an
# imported file) are offered as "keep the current rule".
_CUSTOM_PARTS = {'FREQ', 'INTERVAL', 'BYDAY', 'UNTIL', 'COUNT', 'WKST'}


def parse(rule):
    """'FREQ=WEEKLY;BYDAY=TU' -> {'FREQ': 'WEEKLY', 'BYDAY': 'TU'}"""
    parts = {}
    for piece in filter(None, rule.strip().removeprefix('RRULE:').split(';')):
        key, _, value = piece.partition('=')
        parts[key.strip().upper()] = value.strip().upper()
    return parts


def join(parts):
    order = ['FREQ', 'INTERVAL', 'BYMONTH', 'BYMONTHDAY', 'BYDAY', 'BYSETPOS', 'WKST', 'UNTIL', 'COUNT']
    keys = sorted(parts, key=lambda k: order.index(k) if k in order else len(order))
    return ';'.join(f'{k}={parts[k]}' for k in keys)


def _nth(date):
    """Which weekday-of-month `date` is: 1-4, or -1 for a fifth (the last)."""
    n = (date.day - 1) // 7 + 1
    return -1 if n == 5 else n


def _canonical(rule):
    """Parts of `rule` with no-op settings dropped (INTERVAL=1, a WKST that can't matter)."""
    parts = parse(normalized(rule)) if rule else {}
    if parts.get('INTERVAL') == '1':
        del parts['INTERVAL']
    # The week start only changes anything for weekly rules every 2+ weeks.
    if 'WKST' in parts and not (parts.get('FREQ') == 'WEEKLY' and 'INTERVAL' in parts):
        del parts['WKST']
    return parts


# Rules that give the same first this-many dates (and end at the same point) count as the same.
_COMPARE_OCCURRENCES = 500


def same_rule(a, b, start):
    """Whether two rules give the same dates for a series starting at `start`.

    Rules can be written differently and still mean the same thing, e.g.
    "FREQ=YEARLY" and "FREQ=YEARLY;INTERVAL=1", or a yearly rule that spells
    out the month and day the start date already implies.
    """
    if _canonical(a) == _canonical(b):
        return True
    if not a or not b:
        return False
    if not isinstance(start, datetime.datetime):
        start = timezone.make_aware(datetime.datetime.combine(start, datetime.time.min))
    start = timezone.localtime(start)
    dates = lambda rule: list(itertools.islice(
        rrulestr(normalized(rule), dtstart=start), _COMPARE_OCCURRENCES + 1))
    try:
        return dates(a) == dates(b)
    except ValueError:  # a rule dateutil can't read
        return False


def presets(date):
    """[(choice, rule, label)] for the repeat menu, worded for a start on `date`."""
    weekday = date.weekday()
    nth = _nth(date)
    return [
        ('daily', 'FREQ=DAILY', 'Daily'),
        ('weekly', f'FREQ=WEEKLY;BYDAY={WEEKDAY_CODES[weekday]}', f'Weekly on {WEEKDAY_NAMES[weekday]}'),
        ('monthly_nth', f'FREQ=MONTHLY;BYDAY={nth}{WEEKDAY_CODES[weekday]}',
         f'Monthly on the {ORDINALS[nth]} {WEEKDAY_NAMES[weekday]}'),
        ('monthly_day', f'FREQ=MONTHLY;BYMONTHDAY={date.day}', f'Monthly on day {date.day}'),
        ('yearly', f'FREQ=YEARLY;BYMONTH={date.month};BYMONTHDAY={date.day}',
         f'Annually on {date_format(date, "F j")}'),
        ('weekdays', f'FREQ=WEEKLY;BYDAY={WEEKDAYS_ONLY}', 'Every weekday (Monday to Friday)'),
    ]


def until_value(date):
    """UNTIL for "ends on `date`": the end of that local day, in UTC."""
    end_of_day = timezone.make_aware(datetime.datetime.combine(date, datetime.time(23, 59, 59)))
    return end_of_day.astimezone(datetime.UTC).strftime('%Y%m%dT%H%M%SZ')


def until_date(value):
    """The local date an UNTIL value falls on."""
    if 'T' in value:
        moment = datetime.datetime.strptime(value, '%Y%m%dT%H%M%SZ').replace(tzinfo=datetime.UTC)
        return timezone.localtime(moment).date()
    return datetime.datetime.strptime(value, '%Y%m%d').date()


def build_custom(freq, interval, weekdays, ends, until=None, count=None):
    parts = {'FREQ': freq}
    if interval and interval > 1:
        parts['INTERVAL'] = str(interval)
    if freq == 'WEEKLY' and weekdays:
        parts['BYDAY'] = ','.join(c for c in WEEKDAY_CODES if c in weekdays)
    if ends == 'on' and until:
        parts['UNTIL'] = until_value(until)
    elif ends == 'after' and count:
        parts['COUNT'] = str(count)
    return join(parts)


def form_initial(rule, date):
    """Form field values that show `rule` (for an event starting on `date`)."""
    initial = {'repeat': '', 'repeat_interval': 1, 'repeat_freq': 'WEEKLY',
               'repeat_weekdays': [WEEKDAY_CODES[date.weekday()]], 'repeat_ends': 'never'}
    if not rule:
        return initial
    for choice, preset_rule, _ in presets(date):
        if same_rule(preset_rule, rule, date):
            initial['repeat'] = choice
            return initial
    parts = parse(rule)
    byday = parts.get('BYDAY', '')
    plain_days = all(code in WEEKDAY_CODES for code in byday.split(',')) if byday else True
    if set(parts) <= _CUSTOM_PARTS and parts.get('FREQ') in FREQ_UNITS and (
            parts['FREQ'] == 'WEEKLY' or not byday) and plain_days:
        initial.update(repeat='custom', repeat_freq=parts['FREQ'],
                       repeat_interval=int(parts.get('INTERVAL', 1)))
        if byday:
            initial['repeat_weekdays'] = byday.split(',')
        if 'UNTIL' in parts:
            initial.update(repeat_ends='on', repeat_until=until_date(parts['UNTIL']))
        elif 'COUNT' in parts:
            initial.update(repeat_ends='after', repeat_count=int(parts['COUNT']))
    else:
        initial['repeat'] = 'keep'
    return initial


def describe(rule, date):
    """Plain-English summary, e.g. 'Every 2 weeks on Mon, Wed, until Dec 31, 2026'."""
    if not rule:
        return 'Does not repeat'
    for _, preset_rule, label in presets(date):
        if same_rule(preset_rule, rule, date):
            return label
    parts = parse(rule)
    unit = FREQ_UNITS.get(parts.get('FREQ'))
    if not unit:
        return f'Custom ({rule})'
    interval = int(parts.get('INTERVAL', 1))
    text = f'Every {interval} {unit}s' if interval > 1 else f'Every {unit}'
    byday = parts.get('BYDAY')
    if byday:
        names = []
        for code in byday.split(','):
            prefix, day = code[:-2], code[-2:]
            if day not in WEEKDAY_CODES:
                return f'Custom ({rule})'
            name = WEEKDAY_NAMES[WEEKDAY_CODES.index(day)][:3]
            names.append(f'{ORDINALS.get(int(prefix), prefix)} {name}' if prefix else name)
        text += ' on ' + ', '.join(names)
    if parts.get('BYMONTHDAY'):
        text += f' on day {parts["BYMONTHDAY"]}'
    if 'UNTIL' in parts:
        text += f', until {date_format(until_date(parts["UNTIL"]), "M j, Y")}'
    elif 'COUNT' in parts:
        text += f', {parts["COUNT"]} times'
    return text


def normalized(rule):
    """`rule` with UNTIL in UTC, as dateutil requires for time-zoned starts.

    Files may give UNTIL as a plain date (all-day events) or a local time.
    """
    parts = parse(rule)
    if 'UNTIL' in parts and not parts['UNTIL'].endswith('Z'):
        value = parts['UNTIL']
        if 'T' in value:
            moment = timezone.make_aware(datetime.datetime.strptime(value, '%Y%m%dT%H%M%S'))
            parts['UNTIL'] = moment.astimezone(datetime.UTC).strftime('%Y%m%dT%H%M%SZ')
        else:
            parts['UNTIL'] = until_value(until_date(value))
    return join(parts)


def _until_moment(value):
    if 'T' in value:
        return datetime.datetime.strptime(value, '%Y%m%dT%H%M%SZ').replace(tzinfo=datetime.UTC)
    return timezone.make_aware(datetime.datetime.combine(until_date(value), datetime.time(23, 59, 59)))


def truncated(rule, before):
    """`rule` changed to stop before the moment `before` (for "this and following").

    The caller must check that a COUNT-limited rule actually reaches `before`;
    otherwise dropping COUNT here would make the series longer.
    """
    parts = parse(rule)
    parts.pop('COUNT', None)
    cutoff = before - datetime.timedelta(seconds=1)
    if 'UNTIL' not in parts or _until_moment(parts['UNTIL']) > cutoff:
        parts['UNTIL'] = cutoff.astimezone(datetime.UTC).strftime('%Y%m%dT%H%M%SZ')
    return join(parts)
