"""Hebrew calendar arithmetic and major Jewish holidays (as observed outside Israel).

The conversion follows Reingold & Dershowitz, "Calendrical Calculations".
Dates are handled as day numbers from datetime.date.toordinal() (1 = Jan 1, 1 CE).

Months are numbered from Nisan, as in the Torah, although the year number
changes at Tishrei (month 7):
  1 Nisan, 2 Iyyar, 3 Sivan, 4 Tammuz, 5 Av, 6 Elul, 7 Tishrei, 8 Marheshvan,
  9 Kislev, 10 Tevet, 11 Shevat, 12 Adar (Adar I in leap years), 13 Adar II
"""

import datetime

NISAN, IYYAR, SIVAN, TAMMUZ, AV, ELUL = 1, 2, 3, 4, 5, 6
TISHREI, MARHESHVAN, KISLEV, TEVET, SHEVAT, ADAR, ADAR_II = 7, 8, 9, 10, 11, 12, 13

MON, FRI, SAT, SUN = 0, 4, 5, 6

# Day number of 1 Tishrei, year 1 (Oct 7, 3761 BCE in the Julian calendar).
EPOCH = -1373427


def is_leap_year(year):
    """7 of every 19 years add a second Adar, keeping Passover in spring."""
    return (7 * year + 1) % 19 < 7


def _elapsed_days(year):
    """Days from the epoch to the molad (average new moon) of Tishrei.

    A lunar month is fixed at 29 days 12 hours 793 parts (1 hour = 1080
    parts). Includes the rule that Rosh Hashanah can't fall on a Sunday,
    Wednesday or Friday.
    """
    months = (235 * year - 234) // 19
    parts = 12084 + 13753 * months
    days = 29 * months + parts // 25920
    if (3 * (days + 1)) % 7 < 3:
        days += 1
    return days


def _year_length_correction(year):
    """Further postponement keeping every year 353-355 or 383-385 days long."""
    before, this, after = (_elapsed_days(y) for y in (year - 1, year, year + 1))
    if after - this == 356:
        return 2
    if this - before == 382:
        return 1
    return 0


def new_year(year):
    """Day number of Rosh Hashanah (1 Tishrei) of `year`."""
    return EPOCH + _elapsed_days(year) + _year_length_correction(year)


def _days_in_year(year):
    return new_year(year + 1) - new_year(year)


def _days_in_month(year, month):
    length = _days_in_year(year)
    if month in (IYYAR, TAMMUZ, ELUL, TEVET, ADAR_II):
        return 29
    if month == ADAR and not is_leap_year(year):
        return 29
    if month == MARHESHVAN and length % 10 != 5:  # long only in 355/385-day years
        return 29
    if month == KISLEV and length % 10 == 3:      # short only in 353/383-day years
        return 29
    return 30


def to_gregorian(year, month, day):
    """Convert a Hebrew date to a datetime.date."""
    last_month = ADAR_II if is_leap_year(year) else ADAR
    if month < TISHREI:
        months_before = list(range(TISHREI, last_month + 1)) + list(range(NISAN, month))
    else:
        months_before = range(TISHREI, month)
    ordinal = new_year(year) + sum(_days_in_month(year, m) for m in months_before) + day - 1
    return datetime.date.fromordinal(ordinal)


def holidays_for_year(year):
    """[(date, name)] for the Hebrew year starting at Rosh Hashanah of `year`."""
    def on(month, day, offset=0):
        return to_gregorian(year, month, day) + datetime.timedelta(days=offset)

    days = [
        (on(TISHREI, 1, -1), 'Erev Rosh Hashanah'),
        (on(TISHREI, 1), 'Rosh Hashanah'),
        (on(TISHREI, 2), 'Rosh Hashanah (day 2)'),
        (on(TISHREI, 9), 'Erev Yom Kippur'),
        (on(TISHREI, 10), 'Yom Kippur'),
        *((on(TISHREI, 15, i), f'Sukkot (day {i + 1})') for i in range(7)),
        (on(TISHREI, 22), 'Shemini Atzeret'),
        (on(TISHREI, 23), 'Simchat Torah'),
        *((on(KISLEV, 25, i), f'Hanukkah (day {i + 1})') for i in range(8)),
        (on(SHEVAT, 15), 'Tu BiShvat'),
        (on(ADAR_II if is_leap_year(year) else ADAR, 14), 'Purim'),
        (on(NISAN, 14), 'Erev Passover'),
        *((on(NISAN, 15, i), f'Passover (day {i + 1})') for i in range(8)),
        (on(SIVAN, 6), 'Shavuot'),
        (on(SIVAN, 7), 'Shavuot (day 2)'),
    ]
    tisha_bav = on(AV, 9)
    if tisha_bav.weekday() == SAT:  # a fast can't fall on Shabbat; moves to Sunday
        tisha_bav += datetime.timedelta(days=1)
    days.append((tisha_bav, "Tisha B'Av"))
    days += _modern_israeli_days(year, on)
    return sorted(days)


def _modern_israeli_days(year, on):
    """Days set by Israeli law: a base Hebrew date moved by weekday rules.

    Because Passover (15 Nisan) only falls on Sun/Tue/Thu/Sat, each base date
    has only four possible weekdays, and the rules cover the awkward ones —
    mostly so the day, or preparing for it, doesn't run into Shabbat.
    """
    days = []
    one_day = datetime.timedelta(days=1)

    if year >= 5711:  # 1951
        shoah = on(NISAN, 27)
        if shoah.weekday() == FRI:
            shoah -= one_day
        elif shoah.weekday() == SUN and year >= 5757:  # Sunday rule since 1997
            shoah += one_day
        days.append((shoah, 'Yom HaShoah'))

    if year >= 5709:  # 1949
        atzmaut = on(IYYAR, 5)
        if atzmaut.weekday() == FRI:
            atzmaut -= one_day
        elif atzmaut.weekday() == SAT:
            atzmaut -= 2 * one_day
        elif atzmaut.weekday() == MON and year >= 5764:  # since 2004: keep Memorial Day off Sunday
            atzmaut += one_day
        # Memorial Day is always the day before Independence Day.
        days.append((atzmaut - one_day, 'Yom HaZikaron'))
        days.append((atzmaut, 'Yom HaAtzmaut'))

    if year >= 5728:  # 1968
        days.append((on(IYYAR, 28), 'Yom Yerushalayim'))

    return days


def holidays_between(first, last):
    """[(date, name)] for every Jewish holiday from `first` to `last` inclusive."""
    # Hebrew year N begins in the autumn of Gregorian year N - 3761.
    return [
        (date, name)
        for year in range(first.year + 3760, last.year + 3762)
        for date, name in holidays_for_year(year)
        if first <= date <= last
    ]
