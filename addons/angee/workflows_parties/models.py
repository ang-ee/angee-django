"""Workflow lifecycle contributions for native Party records."""

from __future__ import annotations

from django.db import models, transaction


class DecisionReadableParty(models.Model):
    """Opt native Party records into exact pending Decision read delegation."""

    extends = "parties.Party"
    rebac_grantable = {"reader": "write", "pending_decision": "write"}

    class Meta:
        abstract = True


class PartyHandle(models.Model):
    """Wake workflows retaining this exact association when its review changes."""

    extends = "parties.PartyHandle"
    rebac_grantable = {"pending_decision": "write"}

    class Meta:
        abstract = True

    def _deliver_artifact_runs_on_commit(self) -> None:
        from angee.workflows import engine

        transaction.on_commit(lambda: engine.deliver_artifact(self))

    def confirm(self) -> None:
        """Confirm the association and notify exact artifact-linked workflows."""

        with transaction.atomic():
            super().confirm()
            self._deliver_artifact_runs_on_commit()

    def dismiss(self) -> None:
        """Dismiss the association and notify exact artifact-linked workflows."""

        with transaction.atomic():
            super().dismiss()
            self._deliver_artifact_runs_on_commit()
