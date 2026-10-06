import datetime

from django import forms
from django.utils import timezone

from . import recurrence
from .models import Event, Reminder


class DateInput(forms.DateInput):
    input_type = 'date'

    def __init__(self, **kwargs):
        super().__init__(format='%Y-%m-%d', **kwargs)


class TimeInput(forms.TimeInput):
    input_type = 'time'

    def __init__(self, **kwargs):
        super().__init__(format='%H:%M', **kwargs)


class ColorInput(forms.TextInput):
    input_type = 'color'


class EventForm(forms.ModelForm):
    """The event fields; after validation, `values` holds what to save (see series.FIELDS)."""

    start_date = forms.DateField(label='Start', widget=DateInput())
    start_time = forms.TimeField(label='Start time', required=False, widget=TimeInput())
    end_date = forms.DateField(label='End', widget=DateInput())
    end_time = forms.TimeField(label='End time', required=False, widget=TimeInput())

    repeat = forms.ChoiceField(label='Repeat', required=False)
    repeat_interval = forms.IntegerField(min_value=1, max_value=99, required=False, initial=1,
                                         widget=forms.NumberInput(attrs={'aria-label': 'Repeat every'}))
    repeat_freq = forms.ChoiceField(choices=[(f, f'{u}(s)') for f, u in recurrence.FREQ_UNITS.items()],
                                    required=False, initial='WEEKLY')
    repeat_weekdays = forms.MultipleChoiceField(
        choices=list(zip(recurrence.WEEKDAY_CODES, recurrence.WEEKDAY_NAMES)),
        required=False, widget=forms.CheckboxSelectMultiple)
    repeat_ends = forms.ChoiceField(
        choices=[('never', 'Never'), ('on', 'On'), ('after', 'After')],
        required=False, initial='never', widget=forms.RadioSelect)
    repeat_until = forms.DateField(required=False, widget=DateInput(attrs={'aria-label': 'Ends on'}))
    repeat_count = forms.IntegerField(min_value=1, max_value=999, required=False,
                                      widget=forms.NumberInput(attrs={'aria-label': 'Number of times'}))

    class Meta:
        model = Event
        fields = ['title', 'all_day', 'color', 'description']
        widgets = {
            'color': ColorInput(),
            'description': forms.Textarea(attrs={'rows': 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        event = self.instance
        if event.pk:
            start = timezone.localtime(event.start)
            end = timezone.localtime(event.end)
            self.initial.setdefault('start_date', start.date())
            self.initial.setdefault('end_date', end.date())
            if not event.all_day:
                self.initial.setdefault('start_time', start.time())
                self.initial.setdefault('end_time', end.time())

        # A changed date of a series shows (and keeps) the series' rule.
        self.existing_rule = event.series.rrule if event.series_id else event.rrule
        date = self.initial.get('start_date') or timezone.localdate()
        for name, value in recurrence.form_initial(self.existing_rule, date).items():
            self.initial.setdefault(name, value)

        choices = [('', 'Does not repeat')]
        choices += [(choice, label) for choice, _, label in recurrence.presets(date)]
        if self.existing_rule and self.initial['repeat'] == 'keep':
            choices.append(('keep', recurrence.describe(self.existing_rule, date)))
        choices.append(('custom', 'Custom…'))
        self.fields['repeat'].choices = choices

    def clean_color(self):
        return self.cleaned_data['color'].lower()

    def clean(self):
        cleaned = super().clean()
        start_date, end_date = cleaned.get('start_date'), cleaned.get('end_date')
        if not (start_date and end_date):
            return cleaned

        if cleaned.get('all_day'):
            start_time = end_time = datetime.time.min
        else:
            start_time, end_time = cleaned.get('start_time'), cleaned.get('end_time')
            if start_time is None:
                self.add_error('start_time', 'Enter a start time or mark the event all day.')
            if end_time is None:
                self.add_error('end_time', 'Enter an end time or mark the event all day.')
            if start_time is None or end_time is None:
                return cleaned

        tz = timezone.get_current_timezone()
        start = datetime.datetime.combine(start_date, start_time, tzinfo=tz)
        end = datetime.datetime.combine(end_date, end_time, tzinfo=tz)
        if end < start:
            self.add_error('end_date', 'End must be after start.')
            return cleaned

        rule = self._repeat_rule(cleaned, start_date)
        if rule is not None:
            self.values = {
                'title': cleaned['title'], 'start': start, 'end': end,
                'all_day': cleaned.get('all_day', False), 'color': cleaned['color'],
                'description': cleaned.get('description', ''), 'rrule': rule,
            }
        return cleaned

    REPEAT_FIELDS = {'repeat', 'repeat_interval', 'repeat_freq', 'repeat_weekdays',
                     'repeat_ends', 'repeat_until', 'repeat_count'}

    @property
    def repeat_changed(self):
        """Whether the repeat settings were edited (not just shown)."""
        return bool(self.REPEAT_FIELDS & set(self.changed_data))

    def _repeat_rule(self, cleaned, start_date):
        """The RRULE chosen in the form ('' for none), or None after adding an error."""
        choice = cleaned.get('repeat') or ''
        if choice == '':
            return ''
        if choice == 'keep' or (choice == 'custom' and self.existing_rule and not self.repeat_changed):
            return self.existing_rule  # untouched: keep it exactly as it was
        if choice != 'custom':
            return next(rule for c, rule, _ in recurrence.presets(start_date) if c == choice)

        freq = cleaned.get('repeat_freq') or 'WEEKLY'
        weekdays = cleaned.get('repeat_weekdays') or [recurrence.WEEKDAY_CODES[start_date.weekday()]]
        ends = cleaned.get('repeat_ends') or 'never'
        until, count = cleaned.get('repeat_until'), cleaned.get('repeat_count')
        if ends == 'on' and (until is None or until < start_date):
            self.add_error('repeat_until', 'Choose an end date on or after the start.')
            return None
        if ends == 'after' and not count:
            self.add_error('repeat_count', 'Enter how many times it repeats.')
            return None
        return recurrence.build_custom(
            freq, cleaned.get('repeat_interval') or 1, weekdays, ends, until, count)


class ReminderForm(forms.ModelForm):
    UNIT_CHOICES = [(1, 'minutes'), (60, 'hours'), (60 * 24, 'days'), (60 * 24 * 7, 'weeks')]

    amount = forms.IntegerField(min_value=0, max_value=9999)
    unit = forms.TypedChoiceField(choices=UNIT_CHOICES, coerce=int, initial=1)

    class Meta:
        model = Reminder
        fields = ['method']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            minutes = self.instance.minutes_before
            unit = max(u for u, _ in self.UNIT_CHOICES if minutes % u == 0)
            self.initial.setdefault('amount', minutes // unit)
            self.initial.setdefault('unit', unit)

    def clean(self):
        cleaned = super().clean()
        amount, unit = cleaned.get('amount'), cleaned.get('unit')
        if amount is not None and unit:
            if amount * unit > Reminder.MAX_MINUTES_BEFORE:
                raise forms.ValidationError('Reminders can be at most 4 weeks before.')
            self.instance.minutes_before = amount * unit
        return cleaned


ReminderFormSet = forms.inlineformset_factory(
    Event, Reminder, form=ReminderForm, extra=0, max_num=5, validate_max=True, can_delete=True,
)
