import calendar
import datetime
import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST
from django.views.decorators.vary import vary_on_headers

from . import notifications as notify
from . import series
from .holidays import holidays_between
from .forms import EventForm, ReminderFormSet
from .models import Event, PushSubscription, parse_occurrence_key


@login_required
def month(request, year=None, month=None):
    today = timezone.localdate()
    year = year or today.year
    month = month or today.month
    if not 1 <= month <= 12:
        return redirect('planner:month')

    weeks = calendar.Calendar(firstweekday=6).monthdatescalendar(year, month)
    first_day, last_day = weeks[0][0], weeks[-1][-1]

    tz = timezone.get_current_timezone()
    range_start = datetime.datetime.combine(first_day, datetime.time.min, tzinfo=tz)
    range_end = datetime.datetime.combine(
        last_day + datetime.timedelta(days=1), datetime.time.min, tzinfo=tz
    )
    mine = Event.objects.filter(owner=request.user)
    items = list(mine.filter(rrule='', start__lt=range_end, end__gte=range_start))
    for repeating in mine.exclude(rrule='').filter(start__lt=range_end):
        items += repeating.occurrences_between(range_start, range_end)
    items.sort(key=lambda item: (not item.all_day, item.start))

    # Place each event on every visible day it spans.
    by_day = {}
    for item in items:
        day = max(timezone.localtime(item.start).date(), first_day)
        end_day = min(timezone.localtime(item.end).date(), last_day)
        while day <= end_day:
            by_day.setdefault(day, []).append(item)
            day += datetime.timedelta(days=1)

    holidays = holidays_between(first_day, last_day)

    prev_month = datetime.date(year, month, 1) - datetime.timedelta(days=1)
    next_month = datetime.date(year, month, 28) + datetime.timedelta(days=4)

    return render(request, 'planner/month.html', {
        'month_start': datetime.date(year, month, 1),
        'weeks': [
            [(day, holidays.get(day, []), by_day.get(day, [])) for day in week]
            for week in weeks
        ],
        'weekday_names': ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'],
        'today': today,
        'prev': prev_month,
        'next': next_month,
    })


@login_required
@vary_on_headers('X-Modal')
def event_create(request):
    initial = {}
    date_str = request.GET.get('date')
    if date_str:
        try:
            day = datetime.date.fromisoformat(date_str)
            initial = {
                'start_date': day, 'start_time': datetime.time(9),
                'end_date': day, 'end_time': datetime.time(10),
            }
        except ValueError:
            pass
    return _event_form(request, Event(owner=request.user), initial)


@login_required
@vary_on_headers('X-Modal')
def event_edit(request, pk):
    event = get_object_or_404(Event, pk=pk, owner=request.user)
    return _event_form(request, event, occurrence=_occurrence(request, event))


@login_required
@require_POST
def event_delete(request, pk):
    event = get_object_or_404(Event, pk=pk, owner=request.user)
    occurrence = _occurrence(request, event)
    # Without a choice, only the one date of a series goes.
    scope = request.POST.get('scope') if request.POST.get('scope') in series.SCOPES else series.THIS
    when = occurrence.start if occurrence else event.start
    series.delete_event(event, scope, occurrence.start if occurrence else None)
    return _done(request, timezone.localtime(when))


def _occurrence(request, event):
    """The date of a repeating event chosen by ?occurrence=<key>, if any."""
    key = request.GET.get('occurrence')
    if not key or not event.is_repeating:
        return None
    try:
        occurrence = event.occurrence_at(parse_occurrence_key(key))
    except ValueError:
        occurrence = None
    if occurrence is None:
        raise Http404('No such date in this series')
    return occurrence


def _is_modal(request):
    """Request comes from the month page's pop-up rather than a full page."""
    return request.headers.get('X-Modal') == '1'


def _done(request, start):
    """After a save or delete, go to the month the event is in."""
    url = reverse('planner:month', args=[start.year, start.month])
    if _is_modal(request):
        return JsonResponse({'redirect': url})
    return redirect(url)


def _reminder_rows(formset):
    """[(method, minutes_before)] for the reminder rows kept in the form."""
    rows = []
    for form in formset.forms:
        data = form.cleaned_data
        if data and not data.get('DELETE'):
            rows.append((data['method'], data['amount'] * data['unit']))
    return rows


def _event_form(request, event, initial=None, occurrence=None):
    initial = dict(initial or {})
    if occurrence:  # show the chosen date, not the series' first
        start, end = timezone.localtime(occurrence.start), timezone.localtime(occurrence.end)
        initial.update(start_date=start.date(), end_date=end.date())
        if not event.all_day:
            initial.update(start_time=start.time(), end_time=end.time())
    in_series = bool(event.pk) and (event.is_repeating or event.series_id is not None)

    if request.method == 'POST':
        form = EventForm(request.POST, instance=event, initial=initial)
        reminders = ReminderFormSet(request.POST, instance=event, prefix='reminders')
        scope = request.POST.get('scope') if in_series else series.ALL
        if scope not in series.SCOPES:
            scope = series.THIS
        if form.is_valid() and reminders.is_valid():
            values = form.values
            if in_series and scope == series.THIS and form.repeat_changed:
                form.add_error('repeat', 'A change to how the event repeats can only apply to '
                                         '"this and following" or "all" events.')
            else:
                series.save_event(event, values, _reminder_rows(reminders), scope,
                                  occurrence.start if occurrence else None)
                return _done(request, timezone.localtime(values['start']))
    else:
        form = EventForm(instance=event, initial=initial)
        reminders = ReminderFormSet(instance=event, prefix='reminders')

    query = f'?occurrence={occurrence.key}' if occurrence else ''
    template = '_event_form.html' if _is_modal(request) else 'event_form.html'
    return render(request, f'planner/{template}', {
        'form': form,
        'reminders': reminders,
        'event': event,
        'in_series': in_series,
        'form_action': (reverse('planner:event_edit', args=[event.pk]) if event.pk
                        else reverse('planner:event_create')) + query,
        'delete_action': reverse('planner:event_delete', args=[event.pk]) + query if event.pk else '',
        'preset_colors': Event.PRESET_COLORS,
        'preset_columns': Event.PRESET_COLUMNS,
    })


@login_required
def notification_settings(request):
    return render(request, 'planner/notifications.html', {
        'vapid_public_key': notify.vapid_public_key(),
        'devices': request.user.push_subscriptions.order_by('-created'),
    })


@login_required
@require_POST
def push_subscribe(request):
    try:
        data = json.loads(request.body)
        endpoint, keys = data['endpoint'], data['keys']
        p256dh, auth = keys['p256dh'], keys['auth']
    except (ValueError, KeyError, TypeError):
        return HttpResponseBadRequest('Invalid subscription')
    if not endpoint.startswith('https://'):
        return HttpResponseBadRequest('Invalid endpoint')
    PushSubscription.objects.update_or_create(
        endpoint=endpoint,
        defaults={
            'user': request.user, 'p256dh': p256dh, 'auth': auth,
            'user_agent': request.headers.get('User-Agent', '')[:300],
        },
    )
    return JsonResponse({'ok': True})


@login_required
@require_POST
def push_unsubscribe(request):
    try:
        endpoint = json.loads(request.body)['endpoint']
    except (ValueError, KeyError, TypeError):
        return HttpResponseBadRequest('Invalid request')
    PushSubscription.objects.filter(user=request.user, endpoint=endpoint).delete()
    return JsonResponse({'ok': True})


@login_required
@require_POST
def device_remove(request, pk):
    get_object_or_404(PushSubscription, pk=pk, user=request.user).delete()
    messages.success(request, 'Device removed. It will no longer get notifications.')
    return redirect('planner:notifications')


@login_required
@require_POST
def notification_test(request):
    if request.POST.get('kind') == 'email':
        if notify.send_email(request.user, 'Raphical test email',
                             'Email reminders from your calendar are working.'):
            messages.success(request, f'Test email sent to {request.user.email}.')
        else:
            messages.error(request, 'Your account has no email address.')
    else:
        count = notify.send_push(request.user, 'Raphical', 'Notifications are working.',
                                 url='/', tag='test')
        if count:
            messages.success(request, f'Test notification sent to {count} device(s).')
        else:
            messages.error(request, 'No devices have notifications turned on.')
    return redirect('planner:notifications')


@never_cache
def service_worker(request):
    # Served from the site root so the worker's scope covers every page.
    return render(request, 'planner/sw.js', content_type='application/javascript')
