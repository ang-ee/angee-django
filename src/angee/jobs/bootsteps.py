"""Worker initialization gate, before any worker blueprint step can start."""

from typing import Any

from celery import bootsteps
from django_compose.provision import require_provisioned


class RequireProvisioned(bootsteps.StartStopStep):
    """Refuse an unprovisioned database during blueprint construction."""

    def __init__(self, worker: Any, **kwargs: Any) -> None:
        require_provisioned(using="default", timeout=60)
        super().__init__(worker, **kwargs)
