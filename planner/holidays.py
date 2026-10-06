"""Holidays shown on the calendar: major US and Jewish holidays, computed for any year."""

import datetime
from typing import NamedTuple

from . import hebrew_calendar


class Holiday(NamedTuple):
    name: str
    kind: str  # 'us' or 'jewish'; the month view colors them differently


MON, THU, SUN = 0, 3, 6


def _nth_weekday(year, month, weekday, n):
    """The nth `weekday` of the month; n=-1 means the last one."""
    if n > 0:
        first = datetime.date(year, month, 1)
        return first + datetime.timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
    next_month = datetime.date(year + month // 12, month % 12 + 1, 1)
    last = next_month - datetime.timedelta(days=1)
    return last - datetime.timedelta(days=(last.weekday() - weekday) % 7)


def _easter(year):
    """Western Easter Sunday (Anonymous Gregorian algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    g = (8 * b + 13) // 25
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return datetime.date(year, month, day + 1)


def _observed(date):
    """Federal rule: a Saturday holiday is observed Friday, a Sunday one Monday."""
    if date.weekday() == 5:
        return date - datetime.timedelta(days=1)
    if date.weekday() == 6:
        return date + datetime.timedelta(days=1)
    return date


def holidays_for_year(year):
    """[(date, name)] for one year, including weekend 'observed' days."""
    fixed_federal = [
        (datetime.date(year, 1, 1), "New Year's Day"),
        (datetime.date(year, 7, 4), 'Independence Day'),
        (datetime.date(year, 11, 11), 'Veterans Day'),
        (datetime.date(year, 12, 25), 'Christmas Day'),
    ]
    if year >= 2021:
        fixed_federal.append((datetime.date(year, 6, 19), 'Juneteenth'))

    days = list(fixed_federal)
    days += [
        (date, f'{name} (observed)')
        for date, name in ((_observed(d), n) for d, n in fixed_federal)
        if date not in {d for d, _ in fixed_federal}
    ]
    days += [
        (_nth_weekday(year, 1, MON, 3), 'Martin Luther King Jr. Day'),
        (_nth_weekday(year, 2, MON, 3), "Presidents' Day"),
        (_nth_weekday(year, 5, MON, -1), 'Memorial Day'),
        (_nth_weekday(year, 9, MON, 1), 'Labor Day'),
        (_nth_weekday(year, 10, MON, 2), 'Columbus Day'),
        (_nth_weekday(year, 11, THU, 4), 'Thanksgiving Day'),
        # Widely observed, though not federal holidays.
        (datetime.date(year, 2, 14), "Valentine's Day"),
        (datetime.date(year, 3, 17), "St. Patrick's Day"),
        (_easter(year), 'Easter Sunday'),
        (_nth_weekday(year, 5, SUN, 2), "Mother's Day"),
        (_nth_weekday(year, 6, SUN, 3), "Father's Day"),
        (datetime.date(year, 10, 31), 'Halloween'),
        (datetime.date(year, 12, 24), 'Christmas Eve'),
        (datetime.date(year, 12, 31), "New Year's Eve"),
    ]
    return sorted(days)


def us_holidays_between(first, last):
    """[(date, name)] for every US holiday from `first` to `last` inclusive."""
    # A neighbouring year's observed day can land in this one (e.g. New
    # Year's Day on a Saturday is observed Dec 31 of the year before).
    return [
        (date, name)
        for year in range(first.year - 1, last.year + 2)
        for date, name in holidays_for_year(year)
        if first <= date <= last
    ]


def holidays_between(first, last):
    """{date: [Holiday]} for every holiday from `first` to `last` inclusive."""
    result = {}
    for kind, found in [
        ('us', us_holidays_between(first, last)),
        ('jewish', hebrew_calendar.holidays_between(first, last)),
    ]:
        for date, name in found:
            result.setdefault(date, []).append(Holiday(name, kind))
    return result
