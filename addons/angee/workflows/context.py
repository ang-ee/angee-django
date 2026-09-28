"""Actor-scoped values and record lookups handed to DATABASE steps."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from django.apps import apps
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models

from angee.base.identity import instance_from_public_id
from angee.base.scoping import read_scoped_queryset
from angee.workflows.states import DONE_OUTCOME
from angee.workflows.steps import Done, Fail, Step, Wait


@dataclass
class StepContext:
    """One claimed attempt with its already-resolved class and scoped principal."""

    run: Any
    step_run: Any
    step: type[Step]
    attempt: Any
    input: Any
    config: Any
    actor: Any
    now: datetime

    @property
    def state(self) -> Any:
        """Return the persisted checkpoint from the preceding wait."""
        return self.step_run.state

    @property
    def idempotency_key(self) -> str:
        """Return an identity stable across every attempt of this step run."""
        return f"workflows:step:{self.step_run.pk}"

    @property
    def subject(self) -> models.Model | None:
        """Resolve the run's declared subject through the shared reference owner."""
        return self._subject(lock=False)

    def subject_for_update(self) -> models.Model:
        """Lock the actor-readable subject after the already-held workflow rows."""
        subject = self._subject(lock=True)
        if subject is None:
            raise ValidationError("This run has no subject.")
        return subject

    def _subject(self, *, lock: bool) -> models.Model | None:
        reference = self.run.record_ref
        if reference.object_id is None:
            return None
        if not reference.model_label:
            raise ValidationError("The run subject was deleted.")
        return self.load(apps.get_model(reference.model_label), reference.public_id, lock=lock)

    def load[M: models.Model](
        self,
        model: type[M],
        public_id: str,
        *,
        permission: str = "read",
        lock: bool = False,
    ) -> M:
        """Load a public id through native identity and permission scoping."""
        queryset = read_scoped_queryset(model, self.actor, action=permission)
        if queryset is None:
            raise PermissionDenied(f"{model._meta.label} has no actor-scoped read contract.")
        if lock:
            queryset = queryset.lock_if_supported(no_key=True)
        instance = instance_from_public_id(model, public_id, queryset=queryset)
        if instance is None:
            raise PermissionDenied("The requested record is absent or inaccessible.")
        return instance

    def done(self, output: Any = None, *, outcome: str = DONE_OUTCOME) -> Done:
        """Construct this step's completion for checking at the body boundary."""
        return self.step.done(output, outcome=outcome)

    def wait(self, *, until: datetime, state: Any = None) -> Wait:
        """Wait until an aware time, carrying the supplied checkpoint."""
        return Wait(until=until, state=state)

    def fail(self, message: str) -> Fail:
        """Return a permanent failure; settlement owns its routing outcome."""
        return Fail(error=message)
