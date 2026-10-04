"""Notification after a decision's final verdict commits."""

from django.dispatch import Signal

decision_answered = Signal()
