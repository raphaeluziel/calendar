"""Sending event reminders as Web Push notifications and emails."""

import base64
import datetime
import json
import logging

from cryptography.hazmat.primitives import serialization
from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone
from django.utils.dateformat import format as date_format
from py_vapid import Vapid
from pywebpush import WebPushException, webpush

from .models import Occurrence, PushSubscription, Reminder

logger = logging.getLogger(__name__)

# Reminders that come due while the sender isn't running are dropped once
# they're this late, rather than arriving long after they're useful.
MAX_LATENESS = datetime.timedelta(minutes=15)
# How long a push service holds a message for a device that's offline/asleep.
PUSH_TTL_SECONDS = 60 * 60


def _vapid():
    """The server's VAPID key pair, generated on first use."""
    path = settings.VAPID_PRIVATE_KEY_FILE
    if not path.exists():
        vapid = Vapid()
        vapid.generate_keys()
        vapid.save_key(str(path))
        path.chmod(0o600)
    return Vapid.from_file(str(path))


def vapid_public_key():
    """Public key in the base64url form browsers want as applicationServerKey."""
    raw = _vapid().public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()


def describe_when(event):
    """e.g. 'Tue, Nov 3 · 2:30 PM – 3:30 PM' or 'All day · Tue, Nov 3'."""
    start, end = timezone.localtime(event.start), timezone.localtime(event.end)
    day = 'D, M j'
    if event.all_day:
        if start.date() == end.date():
            return f'All day · {date_format(start, day)}'
        return f'{date_format(start, day)} – {date_format(end, day)}'
    if start.date() == end.date():
        return f'{date_format(start, day)} · {date_format(start, "g:i A")} – {date_format(end, "g:i A")}'
    return f'{date_format(start, day + ", g:i A")} – {date_format(end, day + ", g:i A")}'


def send_push(user, title, body, url='/', tag=None, devices=None):
    """Send a notification to every device the user turned push on for.

    Returns how many devices accepted it. Devices whose subscription has
    expired are forgotten (and removed from `devices`, a list of the user's
    PushSubscriptions the caller may pass to save looking them up).
    """
    payload = json.dumps({'title': title, 'body': body, 'url': url, 'tag': tag})
    vapid = _vapid()
    delivered = 0
    if devices is None:
        devices = list(PushSubscription.objects.filter(user=user))
    for sub in list(devices):
        try:
            webpush(
                sub.as_subscription_info(),
                data=payload,
                vapid_private_key=vapid,
                vapid_claims={'sub': settings.VAPID_SUBJECT},  # webpush mutates this
                ttl=PUSH_TTL_SECONDS,
                headers={'Urgency': 'high'},
                timeout=10,
            )
            delivered += 1
        except WebPushException as exc:
            status = getattr(exc.response, 'status_code', None)
            if status in (404, 410):
                sub.delete()
                devices.remove(sub)
            else:
                logger.warning('Push to %s failed: %s', sub, exc)
    return delivered


def send_email(user, subject, body):
    if not user.email:
        logger.warning('Email reminder skipped: %s has no email address', user)
        return False
    send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [user.email])
    return True


def send_reminder(reminder, devices=None):
    event = reminder.event
    if event.is_repeating:
        # Describe and link to the date this reminder is for, not the series' first.
        event = Occurrence(event, reminder.occurrence_start or event.start)
    when = describe_when(event)
    path = event.edit_url
    if reminder.method == Reminder.PUSH:
        send_push(event.owner, event.title, when, url=path, tag=f'reminder-{reminder.pk}',
                  devices=devices)
    else:
        lines = [event.title, when]
        if event.description:
            lines += ['', event.description]
        lines += ['', settings.SITE_URL.rstrip('/') + path]
        send_email(event.owner, f'Reminder: {event.title} ({when})', '\n'.join(lines))


def send_due_reminders(now=None):
    """Send every reminder whose time has come. Returns the number sent."""
    now = now or timezone.now()
    due = list(
        Reminder.objects.filter(sent_at__isnull=True, remind_at__lte=now)
        .select_related('event__owner').prefetch_related('event__exceptions')
    )
    # Claim one-off reminders up front, in one query: done even if sending
    # fails, so one bad reminder can't repeat every minute.
    Reminder.objects.filter(
        pk__in=[r.pk for r in due if not r.event.is_repeating]).update(sent_at=now)
    # Each user's devices, looked up once for the whole run.
    devices = {}
    for sub in PushSubscription.objects.filter(user__in={r.event.owner_id for r in due}):
        devices.setdefault(sub.user_id, []).append(sub)

    sent = 0
    for reminder in due:
        if now - reminder.remind_at <= MAX_LATENESS:
            try:
                send_reminder(reminder, devices.setdefault(reminder.event.owner_id, []))
                sent += 1
            except Exception:
                logger.exception('Sending %s failed', reminder)
    # Repeating events' reminders move on to their next dates, saved together.
    repeating = [r for r in due if r.event.is_repeating]
    for reminder in repeating:
        reminder.schedule()
    Reminder.objects.bulk_update(repeating, Reminder.SCHEDULE_FIELDS)
    return sent
