"""Transactional events owned by work; receivers may reject the change.

Receivers run inside the sender's transaction, so a failing receiver rolls the
change back. They own any further elevation; external effects wait for commit.
"""

from django.dispatch import Signal

task_woken = Signal()
"""A held triage task was woken; supplies ``task``, ``reason`` and ``message``.

``reason`` is a :class:`angee.work.models.TaskWork.WakeReason` value: ``due``
when the hold's wake date passed, ``reply`` when its awaited reply arrived,
``manual`` when someone resumed the review by hand. ``message`` is the reply
for ``reply`` and ``None`` otherwise. ``task`` is the row as the waker held
it, so a system wake's row carries that wake's elevation; a receiver
authorizes its own writes. The task keeps its triage stage; a consumer that
reviews requests asks its review again here.
"""
