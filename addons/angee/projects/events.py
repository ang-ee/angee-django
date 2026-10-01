"""Transactional lifecycle events owned by projects; receivers may reject the change.

Payload instances have no per-instance sudo and retain the initiating actor when
one exists. Receivers own any further elevation; ambient system context is not
changed by dispatch.
"""

from django.dispatch import Signal

task_promoted = Signal()
"""A task became a project; supplies ``task`` and ``project``."""

milestone_reached = Signal()
"""A milestone received its first historical receipt; supplies ``milestone``."""

project_phase_changed = Signal()
"""A project selected a phase; supplies ``project``, ``previous_milestone``, ``milestone``."""

project_status_changed = Signal()
"""A project changed status; supplies ``project``, ``previous_status``, ``status``."""
