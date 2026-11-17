"""Notifications inside the owning transaction; external effects must wait for commit."""

from django.dispatch import Signal

decision_answered = Signal()
decision_reminder_due = Signal()
