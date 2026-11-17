"""Transactional events owned by work; receivers may reject the change.

Payload instances have no per-instance sudo and retain the initiating actor
when one exists. Receivers own any further elevation; ambient system context
is not changed by dispatch, and external effects wait for commit.
"""

from django.dispatch import Signal

task_woken = Signal()
"""A held triage task was woken; supplies ``task``, ``reason`` and ``message``.

``reason`` is a :class:`angee.work.models.TaskWork.WakeReason` value: ``due``
when the hold's wake date passed, ``reply`` when its awaited reply arrived,
``manual`` when someone resumed the review by hand. ``message`` is the reply
for ``reply`` and ``None`` otherwise. The task keeps its triage stage; a
consumer that reviews requests asks its review again here. A receiver's
refusal rolls back a hand wake; a wake run for many rows (the due job, a
reply) rolls back only that row, which stays held.
"""
