"""Committed connection discovery events contributed to by capability addons."""

from django.dispatch import Signal

binding_finished = Signal()
"""Discovery applied successfully: ``sender=type(instance)``, ``instance=integration``."""
