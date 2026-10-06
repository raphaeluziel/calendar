#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import atexit
import os
import subprocess
import sys


def start_reminder_sender():
    """Run `send_reminders` alongside `runserver` during development.

    Not part of Django's default manage.py. Event reminders are only sent
    while the `send_reminders` command is running, and Django's development
    server has no way to run background jobs itself. Starting it from here
    means `python manage.py runserver` is all that's needed to get reminders
    while developing. manage.py is never used by a production web server,
    so this has no effect there; in production run
    `python manage.py send_reminders` as its own service (or `--once` from cron).
    """
    if sys.argv[1:2] != ['runserver']:
        return
    # runserver's autoreloader re-runs this file in a child process (marked
    # with RUN_MAIN) and restarts it on every code change. Start the sender
    # only from the long-lived parent so there's exactly one.
    if os.environ.get('RUN_MAIN'):
        return
    sender = subprocess.Popen([sys.executable, os.path.abspath(__file__), 'send_reminders'])
    atexit.register(sender.terminate)


def main():
    """Run administrative tasks."""
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    start_reminder_sender()
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
