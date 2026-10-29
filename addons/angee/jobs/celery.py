"""Native Celery application for Angee jobs.

``ANGEE_CELERY_TASK_CLASS`` optionally selects Celery's app-wide task base by
class path. Without that contribution Celery uses its native DjangoTask.
"""

from __future__ import annotations

from celery import Celery
from django.conf import settings

from angee.project import bootstrap

bootstrap()


app = Celery("angee", task_cls=getattr(settings, "ANGEE_CELERY_TASK_CLASS", "celery.contrib.django.task:DjangoTask"))
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
