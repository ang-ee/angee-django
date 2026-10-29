"""Native Celery application for Angee jobs.

``ANGEE_CELERY_TASK_CLASS`` optionally selects Celery's app-wide task base by
class path. Without that contribution Celery uses its native DjangoTask.
"""

from __future__ import annotations

import os

from celery import Celery
from django.conf import settings

from angee.jobs.bootsteps import RequireProvisioned

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings")


app = Celery("angee", task_cls=getattr(settings, "ANGEE_CELERY_TASK_CLASS", "celery.contrib.django.task:DjangoTask"))
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# __init__ gates the entire blueprint; start-order dependencies cannot provide that guarantee.
app.steps["worker"].add(RequireProvisioned)
