"""Notification inside the verdict transaction; external effects must wait for commit."""

from django.dispatch import Signal

decision_answered = Signal()
