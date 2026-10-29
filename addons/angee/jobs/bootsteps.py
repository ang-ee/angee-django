"""Worker initialization gate, before any worker blueprint step can start."""

from typing import Any

from celery import bootsteps

import angee.base


class RequireProvisioned(bootsteps.StartStopStep):
    """Refuse an unprovisioned database during blueprint construction."""

    def __init__(self, worker: Any, **kwargs: Any) -> None:
        angee.base.require_provisioned(using="default", timeout=60)
        super().__init__(worker, **kwargs)
