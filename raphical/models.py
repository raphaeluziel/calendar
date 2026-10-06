import datetime

from dateutil.rrule import rrulestr
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.urls import reverse
from django.utils import timezone

from .recurrence import normalized


def occurrence_key(start):
    """Stable text id for one occurrence of a repeating event (its start, in UTC)."""
    return start.astimezone(datetime.UTC).strftime('%Y%m%dT%H%M%SZ')


def parse_occurrence_key(key):
    """Inverse of occurrence_key; ValueError if it isn't one."""
    return datetime.datetime.strptime(key, '%Y%m%dT%H%M%SZ').replace(tzinfo=datetime.UTC)


# Material Design palette: 12 hues, each in 4 shades (900, 700, 500, 300).
_HUES = [
    ('red', ['#b71c1c', '#d32f2f', '#f44336', '#e57373']),
    ('orange', ['#bf360c', '#e64a19', '#ff5722', '#ff8a65']),
    ('amber', ['#ff6f00', '#ffa000', '#ffc107', '#ffd54f']),
    ('green', ['#1b5e20', '#388e3c', '#4caf50', '#81c784']),
    ('teal', ['#004d40', '#00796b', '#009688', '#4db6ac']),
    ('cyan', ['#006064', '#0097a7', '#00bcd4', '#4dd0e1']),
    ('blue', ['#0d47a1', '#1976d2', '#2196f3', '#64b5f6']),
    ('indigo', ['#1a237e', '#303f9f', '#3f51b5', '#7986cb']),
    ('purple', ['#4a148c', '#7b1fa2', '#9c27b0', '#ba68c8']),
    ('pink', ['#880e4f', '#c2185b', '#e91e63', '#f06292']),
    ('brown', ['#3e2723', '#5d4037', '#795548', '#a1887f']),
    ('gray', ['#212121', '#616161', '#9e9e9e', '#e0e0e0']),
]
_SHADES = ['Dark', 'Deep', '', 'Light']


class Event(models.Model):
    # Row-major: 4 rows (shades) of 12 columns (hues).
    PRESET_COLORS = [
        (shades[row], f'{_SHADES[row]} {hue}'.strip().capitalize())
        for row in range(len(_SHADES))
        for hue, shades in _HUES
    ]
    PRESET_COLUMNS = len(_HUES)
    DEFAULT_COLOR = '#1976d2'

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='events'
    )
    title = models.CharField(max_length=200)
    # For all-day events, start and end are midnight of the first and last
    # (inclusive) days.
    start = models.DateTimeField()
    end = models.DateTimeField()
    all_day = models.BooleanField(default=False)
    color = models.CharField(
        max_length=7,
        default=DEFAULT_COLOR,
        validators=[RegexValidator(r'^#[0-9a-fA-F]{6}$', 'Enter a color like #1a73e8.')],
    )
    description = models.TextField(blank=True)
    created = models.DateTimeField(auto_now_add=True)

    # Repeating events: an iCalendar RRULE value such as "FREQ=WEEKLY;BYDAY=TU"
    # (empty for one-off events). `start`/`end` are the first occurrence; the
    # rest are computed when needed, never stored.
    rrule = models.TextField(blank=True)
    # Occurrences deleted from the series, as occurrence_key() strings.
    exdates = models.JSONField(default=list, blank=True)
    # An occurrence changed on its own ("this event only") is stored as a
    # separate one-off Event pointing at its series and the occurrence it
    # replaces.
    series = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.CASCADE, related_name='exceptions'
    )
    original_start = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-all_day', 'start']

    def __str__(self):
        return self.title

    @property
    def is_repeating(self):
        return bool(self.rrule)

    @property
    def edit_url(self):
        return reverse('raphical:event_edit', args=[self.pk])

    def rule(self):
        # Expanding from the local start keeps the wall-clock time (9:00 AM
        # stays 9:00 AM) across daylight-saving changes.
        return rrulestr(normalized(self.rrule), dtstart=timezone.localtime(self.start))

    def _skipped_keys(self):
        keys = set(self.exdates)
        keys.update(occurrence_key(start) for start in
                    self.exceptions.values_list('original_start', flat=True) if start)
        return keys

    def occurrences_between(self, range_start, range_end):
        """Occurrences overlapping [range_start, range_end)."""
        duration = self.end - self.start
        skipped = self._skipped_keys()
        return [
            Occurrence(self, start)
            for start in self.rule().between(range_start - duration, range_end, inc=True)
            if start < range_end and occurrence_key(start) not in skipped
        ]

    def occurrences_after(self, moment, limit=1000):
        """Occurrences starting after `moment`, in order."""
        skipped = self._skipped_keys()
        for count, start in enumerate(self.rule().xafter(timezone.localtime(moment))):
            if count >= limit:
                return
            if occurrence_key(start) not in skipped:
                yield Occurrence(self, start)

    def occurrence_at(self, start):
        """The occurrence starting at `start`, or None if the series has none then."""
        if occurrence_key(start) in self._skipped_keys():
            return None
        local = timezone.localtime(start)
        if self.rule().between(local, local, inc=True):
            return Occurrence(self, local)
        return None

    @property
    def text_color(self):
        """Dark or light text, whichever reads better on top of `color`."""
        r, g, b = (int(self.color[i:i + 2], 16) for i in (1, 3, 5))
        return '#202124' if (0.299 * r + 0.587 * g + 0.114 * b) / 255 > 0.6 else '#fff'

    def clean(self):
        if self.start and self.end and self.end < self.start:
            raise ValidationError('End must be after start.')


class Occurrence:
    """One date of a repeating event; looks enough like an Event for the templates."""

    def __init__(self, event, start):
        self.event = event
        self.start = start
        self.end = start + (event.end - event.start)

    def __getattr__(self, name):
        # title, color, all_day, description, text_color, ... come from the series.
        return getattr(self.event, name)

    @property
    def key(self):
        return occurrence_key(self.start)

    @property
    def edit_url(self):
        return f'{self.event.edit_url}?occurrence={self.key}'


class Reminder(models.Model):
    PUSH = 'push'
    EMAIL = 'email'
    METHOD_CHOICES = [(PUSH, 'Notification'), (EMAIL, 'Email')]
    MAX_MINUTES_BEFORE = 4 * 7 * 24 * 60  # 4 weeks
    # All-day events have no start time, so their reminders count back from this.
    ALL_DAY_TIME = datetime.time(9)

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='reminders')
    method = models.CharField(max_length=5, choices=METHOD_CHOICES, default=PUSH)
    minutes_before = models.PositiveIntegerField(default=10)
    remind_at = models.DateTimeField(db_index=True)
    # Which occurrence `remind_at` is for (differs from event.start for repeating events).
    occurrence_start = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['minutes_before']

    def __str__(self):
        return f'{self.get_method_display()} {self.minutes_before} min before {self.event}'

    def reminder_time(self, occurrence_start):
        """When to remind about an occurrence starting at `occurrence_start`."""
        start = occurrence_start
        if self.event.all_day:
            start = datetime.datetime.combine(
                timezone.localtime(start).date(), self.ALL_DAY_TIME,
                tzinfo=timezone.get_current_timezone(),
            )
        return start - datetime.timedelta(minutes=self.minutes_before)

    def next_due(self):
        """(occurrence_start, remind_at) this reminder should fire for next.

        One-off events have a single occurrence. For a repeating event it's the
        next occurrence whose reminder time is still ahead; None once the
        series has no more.
        """
        event = self.event
        if not event.is_repeating:
            return event.start, self.reminder_time(event.start)
        now = timezone.now()
        # Start looking early enough to catch all-day occurrences (reminded
        # from 9 AM on their day) whose reminder is still to come.
        search_from = now + datetime.timedelta(minutes=self.minutes_before, days=-1)
        for occurrence in event.occurrences_after(search_from):
            remind_at = self.reminder_time(occurrence.start)
            if remind_at > now:
                return occurrence.start, remind_at
        return None

    def save(self, *args, **kwargs):
        # Re-arm whenever the reminder time moves (event rescheduled, offset
        # changed, repeating event moved on to its next occurrence). A time
        # that's already past is marked done, not sent late.
        due = self.next_due()
        occurrence_start, remind_at = due or (self.event.start, self.reminder_time(self.event.start))
        if remind_at != self.remind_at or due is None:
            self.remind_at, self.occurrence_start = remind_at, occurrence_start
            now = timezone.now()
            self.sent_at = now if due is None or remind_at <= now else None
        super().save(*args, **kwargs)


class PushSubscription(models.Model):
    """A browser/device that has turned on push notifications."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='push_subscriptions'
    )
    endpoint = models.URLField(max_length=1000, unique=True)
    p256dh = models.CharField(max_length=200)
    auth = models.CharField(max_length=100)
    user_agent = models.CharField(max_length=300, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.user_agent or self.endpoint

    def as_subscription_info(self):
        return {'endpoint': self.endpoint, 'keys': {'p256dh': self.p256dh, 'auth': self.auth}}
