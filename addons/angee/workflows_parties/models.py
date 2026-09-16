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

    def confirm(self) -> None:
        """Confirm the association and notify exact artifact-linked workflows."""

        from angee.workflows import engine

        with transaction.atomic():
            super().confirm()
            engine.schedule_artifact_delivery(self)

    def dismiss(self) -> None:
        """Dismiss the association and notify exact artifact-linked workflows."""

        from angee.workflows import engine

        with transaction.atomic():
            super().dismiss()
            engine.schedule_artifact_delivery(self)
