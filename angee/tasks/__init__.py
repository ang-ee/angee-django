"""Framework task seam backed by Celery.

This app owns Angee's deferred and periodic execution tier. Addons declare
Celery tasks in conventional ``tasks.py`` modules and enqueue through the small
Angee seam when they need framework-owned defaults.
"""

from __future__ import annotations

from angee.tasks.celery import app as celery_app
from angee.tasks.enqueue import enqueue_task
from angee.tasks.locks import LockKey, record_lock_key, task_lock, task_lock_is_held

__all__ = ["LockKey", "celery_app", "enqueue_task", "record_lock_key", "task_lock", "task_lock_is_held"]
