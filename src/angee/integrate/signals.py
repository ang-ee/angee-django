"""Committed connection discovery events contributed to by capability addons."""

from django.dispatch import Signal

discovery_finished = Signal()
"""Discovery applied successfully: ``sender=type(instance)``, ``instance=integration``."""
