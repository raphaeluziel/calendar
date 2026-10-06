import datetime
import json
import tempfile
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import notifications
from .models import Event, PushSubscription, Reminder


class CalendarTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('raph', password='pw')

    def post_event(self, url, data, reminders=(), initial_reminders=0):
        """POST the event form, with `reminders` as (method, amount, unit) rows."""
        data = {
            **data,
            'reminders-TOTAL_FORMS': str(initial_reminders + len(reminders)),
            'reminders-INITIAL_FORMS': str(initial_reminders),
        }
        for i, (method, amount, unit) in enumerate(reminders, start=initial_reminders):
            data.update({
                f'reminders-{i}-method': method,
                f'reminders-{i}-amount': str(amount),
                f'reminders-{i}-unit': str(unit),
            })
        return self.client.post(url, data)

    def test_login_required(self):
        for url in [reverse('planner:month'), reverse('planner:event_create')]:
            response = self.client.get(url)
            self.assertRedirects(response, f"{reverse('login')}?next={url}")

    def test_month_view_shows_events(self):
        self.client.force_login(self.user)
        start = timezone.make_aware(datetime.datetime(2026, 10, 6, 9))
        Event.objects.create(
            owner=self.user, title='Dentist', start=start,
            end=start + datetime.timedelta(hours=1),
        )
        response = self.client.get(reverse('planner:month', args=[2026, 10]))
        self.assertContains(response, 'Dentist')
        self.assertContains(response, 'October 2026')

    def test_create_event(self):
        self.client.force_login(self.user)
        response = self.post_event(reverse('planner:event_create'), {
            'title': 'Lunch',
            'start_date': '2026-11-03', 'start_time': '12:00',
            'end_date': '2026-11-03', 'end_time': '13:00',
            'color': '#0b8043',
            'description': '',
        })
        self.assertRedirects(response, reverse('planner:month', args=[2026, 11]))
        event = Event.objects.get()
        self.assertEqual(event.owner, self.user)
        self.assertEqual(event.color, '#0b8043')
        self.assertFalse(event.all_day)
        # Entered times are New York local time (EST in November).
        self.assertEqual(event.start, datetime.datetime(2026, 11, 3, 17, tzinfo=datetime.UTC))

    def test_create_all_day_event_without_times(self):
        self.client.force_login(self.user)
        response = self.post_event(reverse('planner:event_create'), {
            'title': 'Vacation', 'all_day': 'on',
            'start_date': '2026-11-03', 'end_date': '2026-11-05',
            'color': '#d50000',
        })
        self.assertRedirects(response, reverse('planner:month', args=[2026, 11]))
        event = Event.objects.get()
        self.assertTrue(event.all_day)
        month = self.client.get(reverse('planner:month', args=[2026, 11]))
        self.assertContains(month, 'Vacation', count=6)  # 3 days x (title attr + text)

    def test_timed_event_requires_times(self):
        self.client.force_login(self.user)
        response = self.post_event(reverse('planner:event_create'), {
            'title': 'No time', 'start_date': '2026-11-03', 'end_date': '2026-11-03',
            'color': '#d50000',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Event.objects.exists())

    def test_end_before_start_rejected(self):
        self.client.force_login(self.user)
        response = self.post_event(reverse('planner:event_create'), {
            'title': 'Bad',
            'start_date': '2026-11-03', 'start_time': '12:00',
            'end_date': '2026-11-03', 'end_time': '11:00',
            'color': '#d50000',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Event.objects.exists())

    def test_custom_color(self):
        self.client.force_login(self.user)
        self.post_event(reverse('planner:event_create'), {
            'title': 'Custom', 'all_day': 'on',
            'start_date': '2026-11-03', 'end_date': '2026-11-03',
            'color': '#FFEE58',
        })
        event = Event.objects.get()
        self.assertEqual(event.color, '#ffee58')
        self.assertEqual(event.text_color, '#202124')  # dark text on a light color

    def test_invalid_color_rejected(self):
        self.client.force_login(self.user)
        response = self.post_event(reverse('planner:event_create'), {
            'title': 'Bad color', 'all_day': 'on',
            'start_date': '2026-11-03', 'end_date': '2026-11-03',
            'color': 'red',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Event.objects.exists())

    def _make_event(self, **kwargs):
        start = timezone.make_aware(datetime.datetime(2026, 11, 3, 14, 30))
        return Event.objects.create(
            owner=self.user, title='Meeting', start=start,
            end=start + datetime.timedelta(hours=1), color='#388e3c', **kwargs,
        )

    def test_month_links_events_to_edit(self):
        self.client.force_login(self.user)
        event = self._make_event()
        response = self.client.get(reverse('planner:month', args=[2026, 11]))
        self.assertContains(response, reverse('planner:event_edit', args=[event.pk]))

    def test_edit_form_prefilled(self):
        self.client.force_login(self.user)
        event = self._make_event()
        response = self.client.get(reverse('planner:event_edit', args=[event.pk]))
        self.assertContains(response, 'Edit event')
        self.assertContains(response, 'value="Meeting"')
        self.assertContains(response, 'value="2026-11-03"')
        self.assertContains(response, 'value="14:30"')
        self.assertContains(response, 'value="#388e3c"')

    def test_edit_event(self):
        self.client.force_login(self.user)
        event = self._make_event()
        response = self.post_event(reverse('planner:event_edit', args=[event.pk]), {
            'title': 'Moved', 'all_day': 'on',
            'start_date': '2026-12-01', 'end_date': '2026-12-02',
            'color': '#e91e63',
        })
        self.assertRedirects(response, reverse('planner:month', args=[2026, 12]))
        event.refresh_from_db()
        self.assertEqual(Event.objects.count(), 1)
        self.assertEqual((event.title, event.all_day, event.color), ('Moved', True, '#e91e63'))

    def test_cannot_edit_other_users_event(self):
        event = self._make_event()
        other = get_user_model().objects.create_user('other', password='pw')
        self.client.force_login(other)
        response = self.client.get(reverse('planner:event_edit', args=[event.pk]))
        self.assertEqual(response.status_code, 404)

    def test_delete_event(self):
        self.client.force_login(self.user)
        event = self._make_event()
        self.assertContains(
            self.client.get(reverse('planner:event_edit', args=[event.pk])),
            reverse('planner:event_delete', args=[event.pk]),
        )
        response = self.client.post(reverse('planner:event_delete', args=[event.pk]))
        self.assertRedirects(response, reverse('planner:month', args=[2026, 11]))
        self.assertFalse(Event.objects.exists())

    def test_delete_requires_post(self):
        self.client.force_login(self.user)
        event = self._make_event()
        response = self.client.get(reverse('planner:event_delete', args=[event.pk]))
        self.assertEqual(response.status_code, 405)
        self.assertTrue(Event.objects.exists())

    def test_cannot_delete_other_users_event(self):
        event = self._make_event()
        other = get_user_model().objects.create_user('other', password='pw')
        self.client.force_login(other)
        response = self.client.post(reverse('planner:event_delete', args=[event.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Event.objects.exists())


    def test_events_added_by_clicking_a_day_only(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('planner:month', args=[2026, 10]))
        self.assertNotContains(response, '+ Create')
        self.assertContains(response, 'href="/events/new/?date=2026-10-06"')


TZ_NOW = datetime.datetime(2026, 11, 3, 8, 0)  # New York local time


def aware(*args):
    return timezone.make_aware(datetime.datetime(*args))


_vapid_dir = tempfile.TemporaryDirectory()


@override_settings(VAPID_PRIVATE_KEY_FILE=Path(_vapid_dir.name) / 'vapid.pem')
class ReminderTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            'raph', email='raph@example.com', password='pw')
        now = aware(*TZ_NOW.timetuple()[:5])
        patcher = mock.patch('django.utils.timezone.now', return_value=now)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.now = now
        self.client.force_login(self.user)  # after mocking, so the session isn't already expired

    def make_event(self, start=None, all_day=False, minutes=(10,), method=Reminder.PUSH):
        start = start or aware(2026, 11, 3, 14, 30)
        event = Event.objects.create(
            owner=self.user, title='Dentist', start=start, all_day=all_day,
            end=start + datetime.timedelta(hours=1),
        )
        for m in minutes:
            Reminder.objects.create(event=event, minutes_before=m, method=method)
        return event

    def post_event(self, url, data, reminders=(), initial_reminders=0):
        return CalendarTests.post_event(self, url, data, reminders, initial_reminders)

    # --- scheduling

    def test_remind_at_for_timed_event(self):
        event = self.make_event(minutes=(30,))
        self.assertEqual(event.reminders.get().remind_at, aware(2026, 11, 3, 14, 0))

    def test_all_day_reminders_count_back_from_9am(self):
        event = self.make_event(start=aware(2026, 11, 5, 0, 0), all_day=True, minutes=(0, 24 * 60))
        times = [r.remind_at for r in event.reminders.all()]
        self.assertEqual(times, [aware(2026, 11, 5, 9, 0), aware(2026, 11, 4, 9, 0)])

    def test_reminder_already_past_is_not_sent(self):
        event = self.make_event(start=aware(2026, 11, 3, 8, 5), minutes=(10,))
        self.assertIsNotNone(event.reminders.get().sent_at)

    def test_rescheduling_event_rearms_sent_reminder(self):
        event = self.make_event()
        Reminder.objects.update(sent_at=self.now)
        self.post_event(reverse('planner:event_edit', args=[event.pk]), {
            'title': 'Dentist', 'start_date': '2026-11-04', 'start_time': '10:00',
            'end_date': '2026-11-04', 'end_time': '11:00', 'color': '#1976d2',
            'reminders-0-id': str(event.reminders.get().pk),
            'reminders-0-event': str(event.pk),
            'reminders-0-method': 'push', 'reminders-0-amount': '10', 'reminders-0-unit': '1',
        }, initial_reminders=1)
        reminder = event.reminders.get()
        self.assertEqual(reminder.remind_at, aware(2026, 11, 4, 9, 50))
        self.assertIsNone(reminder.sent_at)

    # --- event form

    def test_new_event_form_has_no_reminders(self):
        response = self.client.get(reverse('planner:event_create') + '?date=2026-11-03')
        self.assertContains(response, 'name="reminders-TOTAL_FORMS" value="0"')
        self.assertContains(response, '+ Add notification')

    def test_create_event_with_reminders(self):
        response = self.post_event(reverse('planner:event_create'), {
            'title': 'Flight', 'start_date': '2026-11-10', 'start_time': '18:00',
            'end_date': '2026-11-10', 'end_time': '21:00', 'color': '#1976d2',
        }, reminders=[('push', 30, 1), ('email', 1, 1440)])
        self.assertEqual(response.status_code, 302)
        reminders = Event.objects.get().reminders.all()
        self.assertEqual([(r.method, r.minutes_before) for r in reminders],
                         [('push', 30), ('email', 1440)])

    def test_edit_form_shows_reminder_in_largest_unit(self):
        event = self.make_event(minutes=(120,))
        response = self.client.get(reverse('planner:event_edit', args=[event.pk]))
        self.assertContains(response, 'name="reminders-0-amount" value="2"')
        self.assertContains(response, '<option value="60" selected>hours</option>', html=True)

    def test_remove_reminder(self):
        event = self.make_event()
        reminder = event.reminders.get()
        self.post_event(reverse('planner:event_edit', args=[event.pk]), {
            'title': 'Dentist', 'start_date': '2026-11-03', 'start_time': '14:30',
            'end_date': '2026-11-03', 'end_time': '15:30', 'color': '#1976d2',
            'reminders-0-id': str(reminder.pk), 'reminders-0-event': str(event.pk),
            'reminders-0-method': 'push', 'reminders-0-amount': '10', 'reminders-0-unit': '1',
            'reminders-0-DELETE': 'on',
        }, initial_reminders=1)
        self.assertFalse(Reminder.objects.exists())

    def test_reminder_more_than_4_weeks_rejected(self):
        response = self.post_event(reverse('planner:event_create'), {
            'title': 'X', 'start_date': '2026-12-30', 'start_time': '10:00',
            'end_date': '2026-12-30', 'end_time': '11:00', 'color': '#1976d2',
        }, reminders=[('push', 5, 10080)])
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Event.objects.exists())

    # --- sending

    def advance_to(self, *args):
        timezone.now.return_value = aware(*args)

    @mock.patch('planner.notifications.webpush')
    def test_due_push_reminder_sent_once(self, webpush):
        PushSubscription.objects.create(
            user=self.user, endpoint='https://push.example/abc', p256dh='k', auth='a')
        event = self.make_event()
        self.advance_to(2026, 11, 3, 14, 19)
        self.assertEqual(notifications.send_due_reminders(), 0)
        self.advance_to(2026, 11, 3, 14, 20)
        self.assertEqual(notifications.send_due_reminders(), 1)
        self.assertEqual(notifications.send_due_reminders(), 0)
        payload = json.loads(webpush.call_args.kwargs['data'])
        self.assertEqual(payload['title'], 'Dentist')
        self.assertEqual(payload['body'], 'Tue, Nov 3 · 2:30 PM – 3:30 PM')
        self.assertEqual(payload['url'], reverse('planner:event_edit', args=[event.pk]))

    def test_due_email_reminder_sent(self):
        self.make_event(method=Reminder.EMAIL)
        self.advance_to(2026, 11, 3, 14, 20)
        call_command('send_reminders', once=True, stdout=mock.MagicMock())
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['raph@example.com'])
        self.assertIn('Dentist', mail.outbox[0].subject)

    def test_very_late_reminder_dropped(self):
        self.make_event(method=Reminder.EMAIL)
        self.advance_to(2026, 11, 3, 15, 0)
        self.assertEqual(notifications.send_due_reminders(), 0)
        self.assertEqual(len(mail.outbox), 0)
        self.assertIsNotNone(Reminder.objects.get().sent_at)

    @mock.patch('planner.notifications.webpush')
    def test_expired_subscription_removed(self, webpush):
        PushSubscription.objects.create(
            user=self.user, endpoint='https://push.example/gone', p256dh='k', auth='a')
        webpush.side_effect = notifications.WebPushException(
            'gone', response=mock.Mock(status_code=410))
        self.assertEqual(notifications.send_push(self.user, 'T', 'B'), 0)
        self.assertFalse(PushSubscription.objects.exists())

    # --- settings page and endpoints

    def test_notifications_page(self):
        response = self.client.get(reverse('planner:notifications'))
        key = notifications.vapid_public_key()
        self.assertEqual(len(key), 87)  # 65-byte P-256 point, base64url
        self.assertContains(response, key)
        self.assertContains(response, 'raph@example.com')

    def test_subscribe_and_unsubscribe(self):
        sub = {'endpoint': 'https://push.example/xyz', 'keys': {'p256dh': 'k', 'auth': 'a'}}
        url = reverse('planner:push_subscribe')
        self.client.post(url, json.dumps(sub), content_type='application/json')
        self.client.post(url, json.dumps(sub), content_type='application/json')
        self.assertEqual(PushSubscription.objects.get().user, self.user)
        self.client.post(reverse('planner:push_unsubscribe'),
                         json.dumps({'endpoint': sub['endpoint']}), content_type='application/json')
        self.assertFalse(PushSubscription.objects.exists())

    def test_subscribe_rejects_bad_data(self):
        url = reverse('planner:push_subscribe')
        for body in ['nope', json.dumps({'endpoint': 'http://insecure', 'keys': {'p256dh': 'k', 'auth': 'a'}})]:
            response = self.client.post(url, body, content_type='application/json')
            self.assertEqual(response.status_code, 400)
        self.assertFalse(PushSubscription.objects.exists())

    def test_test_email(self):
        response = self.client.post(reverse('planner:notification_test'), {'kind': 'email'}, follow=True)
        self.assertContains(response, 'Test email sent')
        self.assertEqual(len(mail.outbox), 1)

    def test_service_worker(self):
        response = self.client.get('/sw.js')
        self.assertEqual(response['Content-Type'], 'application/javascript')
        self.assertContains(response, "addEventListener('push'")
        self.assertNotContains(response, "addEventListener('fetch'")


class PopupFormTests(TestCase):
    """The month page loads the add/edit forms into a pop-up via fetch()."""

    MODAL = {'HTTP_X_MODAL': '1'}

    def setUp(self):
        self.user = get_user_model().objects.create_user('raph', password='pw')
        self.client.force_login(self.user)
        start = timezone.make_aware(datetime.datetime(2026, 11, 3, 14, 30))
        self.event = Event.objects.create(
            owner=self.user, title='Meeting', start=start, end=start + datetime.timedelta(hours=1))

    def event_data(self, **overrides):
        return {
            'title': 'Lunch', 'start_date': '2026-12-03', 'start_time': '12:00',
            'end_date': '2026-12-03', 'end_time': '13:00', 'color': '#1976d2',
            'reminders-TOTAL_FORMS': '0', 'reminders-INITIAL_FORMS': '0', **overrides,
        }

    def test_month_page_has_dialog_and_form_script(self):
        response = self.client.get(reverse('planner:month', args=[2026, 11]))
        self.assertContains(response, '<dialog id="event-dialog"')
        self.assertContains(response, 'function initEventForm(form)')

    def test_modal_get_returns_just_the_form(self):
        response = self.client.get(reverse('planner:event_create') + '?date=2026-11-03', **self.MODAL)
        self.assertContains(response, '<form method="post" class="event-form"')
        self.assertContains(response, 'action="/events/new/"')
        self.assertNotContains(response, '<html')
        self.assertIn('X-Modal', response['Vary'])

    def test_full_page_still_works(self):
        response = self.client.get(reverse('planner:event_edit', args=[self.event.pk]))
        self.assertContains(response, '<html')
        self.assertContains(response, f'action="/events/{self.event.pk}/edit/"')
        self.assertContains(response, "initEventForm(document.querySelector('.event-form'))")

    def test_modal_save_returns_redirect_json(self):
        response = self.client.post(reverse('planner:event_create'), self.event_data(), **self.MODAL)
        self.assertEqual(response.json(), {'redirect': reverse('planner:month', args=[2026, 12])})
        self.assertTrue(Event.objects.filter(title='Lunch').exists())

    def test_modal_save_with_errors_returns_form(self):
        response = self.client.post(
            reverse('planner:event_create'), self.event_data(end_time='11:00'), **self.MODAL)
        self.assertContains(response, 'End must be after start.')
        self.assertNotContains(response, '<html')

    def test_modal_delete_returns_redirect_json(self):
        response = self.client.post(
            reverse('planner:event_delete', args=[self.event.pk]), **self.MODAL)
        self.assertEqual(response.json(), {'redirect': reverse('planner:month', args=[2026, 11])})
        self.assertFalse(Event.objects.exists())


class HolidayTests(TestCase):
    def test_2026_dates(self):
        from .holidays import holidays_for_year
        got = {name: date for date, name in holidays_for_year(2026)}
        expected = {
            'Martin Luther King Jr. Day': (1, 19), "Presidents' Day": (2, 16),
            'Easter Sunday': (4, 5), "Mother's Day": (5, 10), 'Memorial Day': (5, 25),
            "Father's Day": (6, 21), 'Independence Day (observed)': (7, 3),
            'Labor Day': (9, 7), 'Columbus Day': (10, 12), 'Thanksgiving Day': (11, 26),
        }
        for name, (month, day) in expected.items():
            self.assertEqual(got[name], datetime.date(2026, month, day), name)

    def test_observed_day_crossing_into_previous_year(self):
        from .holidays import holidays_between
        dec31 = datetime.date(2027, 12, 31)
        names = [h.name for h in holidays_between(dec31, dec31)[dec31]]
        self.assertIn("New Year's Day (observed)", names)

    def test_no_juneteenth_before_2021(self):
        from .holidays import holidays_for_year
        self.assertNotIn('Juneteenth', [name for _, name in holidays_for_year(2020)])

    def test_month_view_shows_holidays(self):
        user = get_user_model().objects.create_user('raph', password='pw')
        self.client.force_login(user)
        response = self.client.get(reverse('planner:month', args=[2026, 11]))
        self.assertContains(response, '<span class="holiday holiday-us"')
        self.assertContains(response, 'title="Thanksgiving Day"')
        self.assertContains(response, 'Veterans Day')


class HebrewCalendarTests(TestCase):
    def test_known_dates(self):
        from .hebrew_calendar import holidays_between
        got = dict((name, date) for date, name in holidays_between(
            datetime.date(2026, 1, 1), datetime.date(2026, 12, 31)))
        expected = {
            'Purim': (3, 3), 'Passover (day 1)': (4, 2), 'Shavuot': (5, 22),
            "Tisha B'Av": (7, 23), 'Rosh Hashanah': (9, 12), 'Yom Kippur': (9, 21),
            'Sukkot (day 1)': (9, 26), 'Simchat Torah': (10, 4), 'Hanukkah (day 1)': (12, 5),
        }
        for name, (month, day) in expected.items():
            self.assertEqual(got[name], datetime.date(2026, month, day), name)

    def test_leap_year_purim_in_adar_ii(self):
        from .hebrew_calendar import holidays_for_year, is_leap_year
        self.assertTrue(is_leap_year(5787))
        purim = dict((n, d) for d, n in holidays_for_year(5787))['Purim']
        self.assertEqual(purim, datetime.date(2027, 3, 23))

    def test_tisha_bav_moves_off_shabbat(self):
        from .hebrew_calendar import holidays_for_year
        tisha_bav = dict((n, d) for d, n in holidays_for_year(5782))["Tisha B'Av"]
        self.assertEqual(tisha_bav, datetime.date(2022, 8, 7))  # 9 Av was Saturday

    def test_rosh_hashanah_never_sun_wed_fri(self):
        from .hebrew_calendar import new_year
        weekdays = {datetime.date.fromordinal(new_year(y)).weekday() for y in range(5600, 6000)}
        self.assertFalse(weekdays & {6, 2, 4})

    def test_month_view_shows_jewish_holidays(self):
        user = get_user_model().objects.create_user('raph', password='pw')
        self.client.force_login(user)
        response = self.client.get(reverse('planner:month', args=[2026, 9]))
        self.assertContains(response, '<span class="holiday holiday-jewish"')
        self.assertContains(response, 'title="Yom Kippur (begins at sundown the evening before)"')

    def test_modern_israeli_days(self):
        from .hebrew_calendar import holidays_for_year
        # (Hebrew year, Yom HaShoah, Yom HaZikaron, Yom HaAtzmaut): one year per rule.
        cases = [
            (5786, (2026, 4, 14), (2026, 4, 21), (2026, 4, 22)),  # no moves
            (5784, (2024, 5, 6), (2024, 5, 13), (2024, 5, 14)),   # Shoah Sun→Mon; Atzmaut Mon→Tue
            (5785, (2025, 4, 24), (2025, 4, 30), (2025, 5, 1)),   # Shoah Fri→Thu; Atzmaut Sat→Thu
            (5782, (2022, 4, 28), (2022, 5, 4), (2022, 5, 5)),    # Atzmaut Fri→Thu
        ]
        for year, shoah, zikaron, atzmaut in cases:
            got = dict((n, d) for d, n in holidays_for_year(year))
            self.assertEqual(got['Yom HaShoah'], datetime.date(*shoah), year)
            self.assertEqual(got['Yom HaZikaron'], datetime.date(*zikaron), year)
            self.assertEqual(got['Yom HaAtzmaut'], datetime.date(*atzmaut), year)
        self.assertEqual(dict((n, d) for d, n in holidays_for_year(5786))['Yom Yerushalayim'],
                         datetime.date(2026, 5, 15))

    def test_modern_days_not_before_they_existed(self):
        from .hebrew_calendar import holidays_for_year
        names = [n for _, n in holidays_for_year(5700)]  # 1939-40
        for name in ['Yom HaShoah', 'Yom HaZikaron', 'Yom HaAtzmaut', 'Yom Yerushalayim']:
            self.assertNotIn(name, names)


class RecurrenceRuleTests(TestCase):
    def test_presets_follow_start_date(self):
        from . import recurrence
        rules = {c: r for c, r, _ in recurrence.presets(datetime.date(2026, 11, 17))}  # 3rd Tue
        self.assertEqual(rules['weekly'], 'FREQ=WEEKLY;BYDAY=TU')
        self.assertEqual(rules['monthly_nth'], 'FREQ=MONTHLY;BYDAY=3TU')
        labels = {c: l for c, _, l in recurrence.presets(datetime.date(2026, 12, 29))}  # 5th Tue
        self.assertEqual(labels['monthly_nth'], 'Monthly on the last Tuesday')

    def test_custom_rule_round_trip(self):
        from . import recurrence
        rule = recurrence.build_custom('WEEKLY', 2, ['WE', 'MO'], 'on', datetime.date(2026, 12, 31))
        self.assertEqual(rule, 'FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,WE;UNTIL=20270101T045959Z')
        initial = recurrence.form_initial(rule, datetime.date(2026, 11, 2))
        self.assertEqual((initial['repeat'], initial['repeat_interval'], initial['repeat_weekdays'],
                          initial['repeat_until']),
                         ('custom', 2, ['MO', 'WE'], datetime.date(2026, 12, 31)))
        self.assertEqual(recurrence.describe(rule, datetime.date(2026, 11, 2)),
                         'Every 2 weeks on Mon, Wed, until Dec 31, 2026')

    def test_unusual_rule_is_kept(self):
        from . import recurrence
        rule = 'FREQ=MONTHLY;BYDAY=MO,TU,WE,TH,FR;BYSETPOS=-1'  # last weekday of the month
        self.assertEqual(recurrence.form_initial(rule, datetime.date(2026, 11, 30))['repeat'], 'keep')

    def test_date_only_until_is_normalized(self):
        from . import recurrence
        self.assertEqual(recurrence.normalized('FREQ=DAILY;UNTIL=20261105'),
                         'FREQ=DAILY;UNTIL=20261106T045959Z')


class RepeatingEventTests(TestCase):
    MODAL = {'HTTP_X_MODAL': '1'}

    def setUp(self):
        self.user = get_user_model().objects.create_user('raph', password='pw')
        self.client.force_login(self.user)
        # Tuesdays at 9:00, starting Oct 27, 2026 (daylight-saving time ends Nov 1).
        start = timezone.make_aware(datetime.datetime(2026, 10, 27, 9, 0))
        self.series = Event.objects.create(
            owner=self.user, title='Standup', start=start, end=start + datetime.timedelta(minutes=30),
            rrule='FREQ=WEEKLY;BYDAY=TU')

    def local(self, *args):
        return timezone.make_aware(datetime.datetime(*args))

    def dates(self, year=2026, month=11, title=None):
        """Local start times of events shown in a month (optionally with a given title)."""
        response = self.client.get(reverse('planner:month', args=[year, month]))
        items = [i for day in response.context['weeks'] for _, _, events in day for i in events]
        return sorted({timezone.localtime(i.start).replace(tzinfo=None) for i in items
                       if title is None or i.title == title})

    def occurrence_url(self, name, *start, event=None):
        from .models import occurrence_key
        event = event or self.series
        return reverse(f'planner:{name}', args=[event.pk]) + f'?occurrence={occurrence_key(self.local(*start))}'

    def form_data(self, **overrides):
        data = {
            'title': 'Standup', 'start_date': '2026-11-10', 'start_time': '09:00',
            'end_date': '2026-11-10', 'end_time': '09:30', 'color': '#1976d2',
            'repeat': 'weekly', 'repeat_interval': '1', 'repeat_freq': 'WEEKLY',
            'repeat_weekdays': ['TU'], 'repeat_ends': 'never',
            'reminders-TOTAL_FORMS': '0', 'reminders-INITIAL_FORMS': '0',
        }
        data.update(overrides)
        return data

    def test_shows_every_week_at_same_local_time_across_dst(self):
        expected = [datetime.datetime(2026, 11, d, 9, 0) for d in (3, 10, 17, 24)]
        self.assertEqual([d for d in self.dates() if d.month == 11], expected)
        response = self.client.get(reverse('planner:month', args=[2026, 11]))
        self.assertContains(response, 'aria-label="Repeats"')

    def test_create_repeating_event_from_form(self):
        response = self.client.post(reverse('planner:event_create'), self.form_data(
            title='Yoga', start_date='2026-11-02', end_date='2026-11-02',
            repeat='custom', repeat_interval='2', repeat_weekdays=['MO', 'WE'],
            repeat_ends='after', repeat_count='4'), **self.MODAL)
        self.assertEqual(response.status_code, 200)
        yoga = Event.objects.get(title='Yoga')
        self.assertEqual(yoga.rrule, 'FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,WE;COUNT=4')
        self.assertEqual([d.day for d in self.dates(title='Yoga')], [2, 4, 16, 18])

    def test_edit_form_shows_chosen_date(self):
        response = self.client.get(self.occurrence_url('event_edit', 2026, 11, 17, 9, 0), **self.MODAL)
        self.assertContains(response, 'value="2026-11-17"')
        self.assertContains(response, 'data-series')
        self.assertContains(response, '<option value="weekly" selected>Weekly on Tuesday</option>', html=True)

    def test_unknown_date_is_404(self):
        response = self.client.get(self.occurrence_url('event_edit', 2026, 11, 18, 9, 0))
        self.assertEqual(response.status_code, 404)

    def test_edit_this_event_only(self):
        self.client.post(self.occurrence_url('event_edit', 2026, 11, 10, 9, 0), self.form_data(
            title='Standup (moved)', start_date='2026-11-11', end_date='2026-11-11', scope='this'),
            **self.MODAL)
        dates = self.dates()
        self.assertNotIn(datetime.datetime(2026, 11, 10, 9, 0), dates)
        self.assertIn(datetime.datetime(2026, 11, 11, 9, 0), dates)
        self.assertIn(datetime.datetime(2026, 11, 17, 9, 0), dates)
        moved = Event.objects.get(title='Standup (moved)')
        self.assertEqual((moved.series, moved.rrule), (self.series, ''))

    def test_this_event_only_cannot_change_the_repeat(self):
        response = self.client.post(self.occurrence_url('event_edit', 2026, 11, 10, 9, 0),
                                    self.form_data(repeat='daily', scope='this'), **self.MODAL)
        self.assertContains(response, 'can only apply to')
        self.assertEqual(Event.objects.count(), 1)

    def test_edit_this_and_following(self):
        self.client.post(self.occurrence_url('event_edit', 2026, 11, 17, 9, 0), self.form_data(
            start_date='2026-11-17', end_date='2026-11-17', start_time='10:00', end_time='10:30',
            scope='following'), **self.MODAL)
        nov = [d for d in self.dates() if d.month == 11]
        self.assertEqual(nov, [datetime.datetime(2026, 11, 3, 9, 0), datetime.datetime(2026, 11, 10, 9, 0),
                               datetime.datetime(2026, 11, 17, 10, 0), datetime.datetime(2026, 11, 24, 10, 0)])
        self.assertEqual(Event.objects.count(), 2)

    def test_following_splits_count(self):
        self.series.rrule = 'FREQ=WEEKLY;BYDAY=TU;COUNT=6'  # Oct 27 .. Dec 1
        self.series.save()
        self.client.post(self.occurrence_url('event_edit', 2026, 11, 10, 9, 0), self.form_data(
            title='Later', repeat='custom', repeat_ends='after', repeat_count='6', scope='following'),
            **self.MODAL)
        first = Event.objects.get(pk=self.series.pk)
        self.assertEqual(len(list(first.rule())), 2)          # Oct 27, Nov 3
        later = Event.objects.get(title='Later')
        self.assertEqual(len(list(later.rule())), 4)          # Nov 10 .. Dec 1

    def test_edit_all_keeps_changed_dates_attached(self):
        # Skip Nov 3, then move the whole series an hour later from Nov 10's form.
        self.client.post(self.occurrence_url('event_delete', 2026, 11, 3, 9, 0), {'scope': 'this'})
        self.client.post(self.occurrence_url('event_edit', 2026, 11, 10, 9, 0), self.form_data(
            start_time='10:00', end_time='10:30', scope='all'), **self.MODAL)
        nov = [d for d in self.dates() if d.month == 11]
        self.assertEqual(nov, [datetime.datetime(2026, 11, d, 10, 0) for d in (10, 17, 24)])
        self.assertEqual(Event.objects.count(), 1)

    def test_delete_this_following_and_all(self):
        self.client.post(self.occurrence_url('event_delete', 2026, 11, 10, 9, 0), {'scope': 'this'})
        self.assertNotIn(datetime.datetime(2026, 11, 10, 9, 0), self.dates())
        self.client.post(self.occurrence_url('event_delete', 2026, 11, 24, 9, 0), {'scope': 'following'})
        self.assertEqual([d for d in self.dates() if d.month == 11],
                         [datetime.datetime(2026, 11, 3, 9, 0), datetime.datetime(2026, 11, 17, 9, 0)])
        self.assertEqual(self.dates(2026, 12), [])
        self.client.post(self.occurrence_url('event_delete', 2026, 11, 17, 9, 0), {'scope': 'all'})
        self.assertFalse(Event.objects.exists())

    def test_delete_all_removes_changed_dates_too(self):
        self.client.post(self.occurrence_url('event_edit', 2026, 11, 10, 9, 0),
                         self.form_data(title='Moved', scope='this'), **self.MODAL)
        moved = Event.objects.get(title='Moved')
        self.client.post(reverse('planner:event_delete', args=[moved.pk]), {'scope': 'all'})
        self.assertFalse(Event.objects.exists())


class RepeatingReminderTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('raph', email='r@example.com', password='pw')
        self.now = timezone.make_aware(datetime.datetime(2026, 11, 3, 8, 0))  # Tue 8:00
        patcher = mock.patch('django.utils.timezone.now', return_value=self.now)
        patcher.start()
        self.addCleanup(patcher.stop)
        start = timezone.make_aware(datetime.datetime(2026, 10, 27, 9, 0))
        self.series = Event.objects.create(
            owner=self.user, title='Standup', start=start, end=start + datetime.timedelta(minutes=30),
            rrule='FREQ=WEEKLY;BYDAY=TU')
        self.reminder = Reminder.objects.create(event=self.series, method=Reminder.EMAIL, minutes_before=10)

    def local(self, *args):
        return timezone.make_aware(datetime.datetime(*args))

    def test_reminder_targets_next_occurrence(self):
        self.assertEqual(self.reminder.remind_at, self.local(2026, 11, 3, 8, 50))
        self.assertIsNone(self.reminder.sent_at)

    def test_after_sending_moves_to_next_week(self):
        timezone.now.return_value = self.local(2026, 11, 3, 8, 50)
        self.assertEqual(notifications.send_due_reminders(), 1)
        self.assertIn('Tue, Nov 3', mail.outbox[0].subject)
        self.reminder.refresh_from_db()
        self.assertEqual(self.reminder.remind_at, self.local(2026, 11, 10, 8, 50))
        self.assertIsNone(self.reminder.sent_at)

    def test_skipped_date_is_not_reminded(self):
        from .models import occurrence_key
        self.series.exdates = [occurrence_key(self.local(2026, 11, 3, 9, 0))]
        self.series.save()
        self.reminder.save()
        self.assertEqual(self.reminder.remind_at, self.local(2026, 11, 10, 8, 50))

    def test_series_end_stops_reminders(self):
        self.series.rrule = 'FREQ=WEEKLY;BYDAY=TU;COUNT=1'  # only Oct 27, already past
        self.series.save()
        self.reminder.save()
        self.assertIsNotNone(self.reminder.sent_at)
        self.assertEqual(notifications.send_due_reminders(), 0)


ICS_FIRST = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:dentist-1
SUMMARY:Dentist appointment
DESCRIPTION:Bring insurance card
DTSTART;TZID=America/New_York:20261103T143000
DTEND;TZID=America/New_York:20261103T153000
BEGIN:VALARM
ACTION:DISPLAY
TRIGGER:-PT15M
END:VALARM
END:VEVENT
BEGIN:VEVENT
UID:vacation-1
SUMMARY:Vacation
DTSTART;VALUE=DATE:20261110
DTEND;VALUE=DATE:20261113
END:VEVENT
BEGIN:VEVENT
UID:utc-1
SUMMARY:Call with London
DTSTART:20261105T150000Z
DTEND:20261105T160000Z
END:VEVENT
BEGIN:VEVENT
UID:standup-1
SUMMARY:Standup
DTSTART;TZID=America/New_York:20261027T090000
DTEND;TZID=America/New_York:20261027T093000
RRULE:FREQ=WEEKLY;BYDAY=TU;UNTIL=20261231T235959Z
EXDATE;TZID=America/New_York:20261110T090000
END:VEVENT
BEGIN:VEVENT
UID:standup-1
RECURRENCE-ID;TZID=America/New_York:20261117T090000
SUMMARY:Standup (late)
DTSTART;TZID=America/New_York:20261117T100000
DTEND;TZID=America/New_York:20261117T103000
END:VEVENT
BEGIN:VEVENT
UID:standup-1
RECURRENCE-ID;TZID=America/New_York:20261124T090000
STATUS:CANCELLED
SUMMARY:Standup
DTSTART;TZID=America/New_York:20261124T090000
DTEND;TZID=America/New_York:20261124T093000
END:VEVENT
END:VCALENDAR
"""

ICS_SECOND = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//other//EN
BEGIN:VEVENT
UID:other-dentist
SUMMARY:Dentist Appt
DESCRIPTION:Parking on 3rd St
DTSTART;TZID=America/New_York:20261103T143000
DTEND;TZID=America/New_York:20261103T160000
END:VEVENT
BEGIN:VEVENT
UID:other-gym
SUMMARY:Gym
DTSTART;TZID=America/New_York:20261104T070000
DTEND;TZID=America/New_York:20261104T080000
END:VEVENT
END:VCALENDAR
"""


class IcsImportTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('raph', password='pw')
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.first = self.write('first.ics', ICS_FIRST)
        self.second = self.write('second.ics', ICS_SECOND)

    def write(self, name, text):
        path = Path(self.dir.name) / name
        path.write_text(text.replace('\n', '\r\n'))
        return str(path)

    def run_import(self, *args, answers=()):
        import io
        out = io.StringIO()
        with mock.patch('builtins.input', side_effect=list(answers)) as fake_input:
            call_command('import_ics', *args, stdout=out)
        self.prompts = fake_input.call_count
        return out.getvalue()

    def shown(self, year=2026, month=11):
        self.client.force_login(self.user)
        response = self.client.get(reverse('planner:month', args=[year, month]))
        return sorted((timezone.localtime(i.start).strftime('%m-%d %H:%M'), i.title)
                      for week in response.context['weeks'] for _, _, events in week for i in events)

    def test_first_import(self):
        output = self.run_import(self.first)
        self.assertIn('4 event(s), 1 of them repeating', output)
        self.assertIn('4 added', output)
        shown = self.shown()
        self.assertIn(('11-03 09:00', 'Standup'), shown)
        self.assertNotIn(('11-10 09:00', 'Standup'), shown)      # EXDATE
        self.assertIn(('11-17 10:00', 'Standup (late)'), shown)  # changed date
        self.assertNotIn(('11-17 09:00', 'Standup'), shown)
        self.assertNotIn(('11-24 09:00', 'Standup'), shown)      # cancelled date
        self.assertIn(('11-05 10:00', 'Call with London'), shown)  # UTC -> New York
        self.assertEqual([d for d, t in shown if t == 'Vacation'], ['11-10 00:00'] * 3)

        standup = Event.objects.get(title='Standup')
        self.assertEqual(standup.rrule, 'FREQ=WEEKLY;BYDAY=TU;UNTIL=20261231T235959Z')
        self.assertEqual(Event.objects.get(title='Standup (late)').series, standup)
        dentist = Event.objects.get(title='Dentist appointment')
        self.assertEqual([(r.method, r.minutes_before) for r in dentist.reminders.all()],
                         [(Reminder.PUSH, 15)])
        vacation = Event.objects.get(title='Vacation')
        self.assertEqual(timezone.localtime(vacation.end).date(), datetime.date(2026, 11, 12))

    def test_reimport_same_file_skips_everything_without_asking(self):
        self.run_import(self.first)
        output = self.run_import(self.first)
        self.assertEqual(self.prompts, 0)
        self.assertIn('4 skipped (already on the calendar)', output)
        self.assertEqual(Event.objects.count(), 5)  # 4 + the changed Standup date

    def test_duplicate_keep_existing(self):
        self.run_import(self.first)
        output = self.run_import(self.second, answers=['1'])
        self.assertEqual(self.prompts, 1)
        self.assertIn('Possible duplicate on Tue, Nov 3, 2026', output)
        self.assertIn('1 duplicates: kept existing', output)
        self.assertFalse(Event.objects.filter(title='Dentist Appt').exists())
        self.assertTrue(Event.objects.filter(title='Gym').exists())

    def test_duplicate_keep_imported(self):
        self.run_import(self.first)
        self.run_import(self.second, answers=['2'])
        self.assertFalse(Event.objects.filter(title='Dentist appointment').exists())
        self.assertTrue(Event.objects.filter(title='Dentist Appt').exists())

    def test_duplicate_keep_both(self):
        self.run_import(self.first)
        self.run_import(self.second, answers=['b'])
        self.assertEqual(Event.objects.filter(title__startswith='Dentist').count(), 2)

    def test_duplicate_merge_asks_each_difference(self):
        self.run_import(self.first)
        # merge; title: imported; time: existing; description: both
        output = self.run_import(self.second, answers=['m', '2', '1', '3'])
        self.assertEqual(self.prompts, 4)
        self.assertIn('Title differs', output)
        dentist = Event.objects.get(title='Dentist Appt')
        self.assertEqual(timezone.localtime(dentist.end).hour, 15)
        self.assertEqual(dentist.description, 'Bring insurance card\n\nParking on 3rd St')
        self.assertEqual(dentist.reminders.count(), 1)

    def test_invalid_answer_asks_again(self):
        self.run_import(self.first)
        output = self.run_import(self.second, answers=['x', '1'])
        self.assertIn('Please type one of', output)
        self.assertEqual(self.prompts, 2)

    def test_on_duplicate_option_skips_questions(self):
        self.run_import(self.first)
        self.run_import(self.second, '--on-duplicate', 'merge')
        self.assertEqual(self.prompts, 0)
        dentist = Event.objects.get(title='Dentist appointment')  # merge keeps existing values
        self.assertEqual(dentist.description, 'Bring insurance card\n\nParking on 3rd St')

    def test_ctrl_c_changes_nothing(self):
        from django.core.management.base import CommandError
        self.run_import(self.first)
        before = Event.objects.count()
        with self.assertRaisesMessage(CommandError, 'nothing was changed'):
            self.run_import(self.second, answers=[KeyboardInterrupt])
        self.assertEqual(Event.objects.count(), before)

    def test_dry_run_saves_nothing(self):
        output = self.run_import(self.first, '--dry-run')
        self.assertIn('Dry run, nothing saved', output)
        self.assertFalse(Event.objects.exists())

    def test_changed_repeat_rule_is_a_duplicate_to_resolve(self):
        self.run_import(self.first)
        twice_weekly = ICS_FIRST.replace('BYDAY=TU;', 'BYDAY=TU,TH;')
        path = self.write('third.ics', twice_weekly)
        output = self.run_import(path, answers=['m', '2'])  # merge; repeat: imported
        self.assertIn('Repeats:', output)
        self.assertIn('BYDAY=TU,TH', Event.objects.get(title='Standup').rrule)

    def test_bad_file(self):
        from django.core.management.base import CommandError
        path = self.write('bad.ics', 'not a calendar')
        with self.assertRaisesMessage(CommandError, "isn't a valid .ics file"):
            self.run_import(path)
        with self.assertRaisesMessage(CommandError, "Can't read"):
            self.run_import(str(Path(self.dir.name) / 'missing.ics'))


class IcsExportTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('raph', email='r@example.com', password='pw')
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / 'out.ics'

        local = lambda *a: timezone.make_aware(datetime.datetime(*a))
        dentist = Event.objects.create(
            owner=self.user, title='Dentist', start=local(2026, 11, 3, 14, 30),
            end=local(2026, 11, 3, 15, 30), description='Bring card', color='#d32f2f')
        Reminder.objects.create(event=dentist, method=Reminder.PUSH, minutes_before=15)
        Reminder.objects.create(event=dentist, method=Reminder.EMAIL, minutes_before=1440)
        Event.objects.create(owner=self.user, title='Vacation', all_day=True,
                             start=local(2026, 11, 10, 0, 0), end=local(2026, 11, 12, 0, 0))
        self.standup = Event.objects.create(
            owner=self.user, title='Standup', start=local(2026, 10, 27, 9, 0),
            end=local(2026, 10, 27, 9, 30), rrule='FREQ=WEEKLY;BYDAY=TU')
        from . import series
        series.delete_event(self.standup, series.THIS, local(2026, 11, 10, 9, 0))
        moved = series.save_event(self.standup, {
            'title': 'Standup (late)', 'start': local(2026, 11, 17, 10, 0),
            'end': local(2026, 11, 17, 10, 30), 'all_day': False, 'color': '#1976d2',
            'description': '', 'rrule': 'FREQ=WEEKLY;BYDAY=TU'}, [], series.THIS, local(2026, 11, 17, 9, 0))
        self.assertEqual(moved.original_start, local(2026, 11, 17, 9, 0))

    def export(self, *args):
        import io
        out = io.StringIO()
        call_command('export_ics', str(self.path), *args, stdout=out)
        return out.getvalue()

    def test_file_contents(self):
        output = self.export()
        self.assertIn('Exported 3 event(s)', output)
        text = self.path.read_text()
        for expected in [
            'BEGIN:VTIMEZONE', 'TZID:America/New_York',
            'DTSTART;TZID=America/New_York:20261103T143000',
            'DTSTART;VALUE=DATE:20261110', 'DTEND;VALUE=DATE:20261113',  # exclusive end
            'RRULE:FREQ=WEEKLY;BYDAY=TU',
            'EXDATE;TZID=America/New_York:20261110T090000',
            'RECURRENCE-ID;TZID=America/New_York:20261117T090000',
            'TRIGGER:-PT15M', 'ACTION:EMAIL', 'ATTENDEE:mailto:r@example.com',
            'X-PLANNER-COLOR:#d32f2f',
        ]:
            self.assertIn(expected, text)
        self.assertEqual(text.count(f'UID:planner-event-{self.standup.pk}'), 2)  # series + changed date

    def test_round_trip_into_empty_calendar(self):
        self.export()
        other = get_user_model().objects.create_user('copy', password='pw')
        import io
        call_command('import_ics', str(self.path), '--user', 'copy', stdout=io.StringIO())
        copy = lambda u: sorted(
            (e.title, e.start, e.end, e.all_day, e.color, e.description, e.rrule, sorted(e.exdates),
             e.original_start, sorted((r.method, r.minutes_before) for r in e.reminders.all()))
            for e in Event.objects.filter(owner=u))
        self.assertEqual(copy(other), copy(self.user))

    def test_reimport_into_same_calendar_is_all_duplicates(self):
        self.export()
        import io
        out = io.StringIO()
        with mock.patch('builtins.input') as fake_input:
            call_command('import_ics', str(self.path), '--user', 'raph', stdout=out)
        fake_input.assert_not_called()
        self.assertIn('3 skipped (already on the calendar)', out.getvalue())

    def test_refuses_to_overwrite_without_force(self):
        from django.core.management.base import CommandError
        self.path.write_text('keep me')
        with self.assertRaisesMessage(CommandError, 'already exists'):
            self.export()
        self.assertEqual(self.path.read_text(), 'keep me')
        self.export('--force')
        self.assertIn('BEGIN:VCALENDAR', self.path.read_text())


class OldExportColorTests(TestCase):
    def test_color_from_file_exported_before_rename(self):
        from .ics_import import read_ics
        ics = ICS_SECOND.replace('SUMMARY:Gym', 'SUMMARY:Gym\nX-RAPHICAL-COLOR:#388E3C')
        gym = next(i for i in read_ics(ics.replace('\n', '\r\n').encode()) if i.title == 'Gym')
        self.assertEqual(gym.color, '#388e3c')
