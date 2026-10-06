"""Shared base for this app's management commands."""

import argparse

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError, DjangoHelpFormatter


class UsageFormatter(DjangoHelpFormatter, argparse.RawDescriptionHelpFormatter):
    """Django's help layout, keeping the line breaks in `help` and `usage_text`."""


class CalendarCommand(BaseCommand):
    # Longer explanation and examples, shown after the options by
    # `python manage.py help <command>` (or `<command> --help`).
    usage_text = ''

    def create_parser(self, prog_name, subcommand, **kwargs):
        kwargs.setdefault('formatter_class', UsageFormatter)
        kwargs.setdefault('epilog', self.usage_text.strip() or None)
        return super().create_parser(prog_name, subcommand, **kwargs)

    def get_owner(self, username=None):
        """The user named `username`, or the only user if none is given."""
        users = get_user_model().objects.all()
        if username:
            try:
                return users.get(username=username)
            except users.model.DoesNotExist:
                raise CommandError(f'No user named {username!r}.')
        if users.count() != 1:
            raise CommandError('There is more than one user; say which with --user.')
        return users.get()
