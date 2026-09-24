"""Workflow lifecycle contributions for native Party records."""

from __future__ import annotations

from django.db import models, transaction


class DecisionReadableParty(models.Model):
    """Opt native Party records into exact pending Decision read delegation."""

    extends = "parties.Party"
    rebac_grantable = {"reader": "write", "pending_decision": "write"}

    class Meta:
        abstract = True


class Handle(models.Model):
    """Publish the stable collection owner after its Party links resolve."""

    extends = "parties.Handle"
    rebac_grantable = {"pending_decision": "write"}

    class Meta:
        abstract = True

    def _party_links_resolved(self) -> None:
        """Retain delivery only after the parties owner completes resolution."""

        from angee.workflows import engine

        super()._party_links_resolved()
        engine.schedule_artifact_delivery(self)


class PartyHandle(models.Model):
    """Wake workflows retaining this association or its stable Handle owner."""

    extends = "parties.PartyHandle"
    rebac_grantable = {"pending_decision": "write"}

    class Meta:
        abstract = True

    def _resolve_link(self) -> None:
        """Resolve derived authority, then retain this exact link transition."""

        from angee.workflows import engine

        with transaction.atomic():
            super()._resolve_link()
            engine.schedule_artifact_delivery(self)
