"""Explicit maintenance transition from ceremony tuples to live disclosure."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from rebac import SubjectRef, system_context, to_object_ref
from rebac.backends import backend
from rebac.models import active_relationship_model


@dataclass(frozen=True)
class DisclosureChange:
    """One inspectable transition, named by stable database identities."""

    operation: str
    resource: str
    subject: str = ""
    blocker: bool = False


class DisclosureTransition:
    """Preserve shells and receipts, removing only the former ceremony's tuple set.

    Run after the receipt migration and before the new permission schema sync.
    Ambiguous requester and group invitation rows require a manager's resolution.
    """

    def __init__(self) -> None:
        self.rounds = apps.get_model("proposals", "Round")
        self.proposals = apps.get_model("proposals", "Proposal")
        self.relationships = active_relationship_model()

    def changes(self, *, apply: bool = False) -> Iterator[DisclosureChange]:
        """Stream affected subjects and exact operations; preview never writes."""
        with system_context(reason="proposals.disclosure.transition"):
            for round in self.rounds._base_manager.order_by("pk").iterator(chunk_size=500):
                ref = to_object_ref(round)
                shells = list(self.proposals._base_manager.filter(round=round).order_by("pk"))
                holder_ids = {str(row.responder_id) for row in shells if row.responder_id is not None}
                requester_id = None
                if round.requester_party_id is not None:
                    requester_id = (
                        apps.get_model("parties", "Person")
                        ._base_manager.filter(
                            pk=round.requester_party_id,
                        )
                        .values_list("user_id", flat=True)
                        .first()
                    )
                invitations = self.relationships.objects.filter(
                    resource_type=ref.resource_type,
                    resource_id=ref.resource_id,
                    relation__in=("responder", "requester"),
                ).order_by("pk")
                for share in invitations.iterator(chunk_size=500):
                    direct = share.subject_type == "auth/user" and not share.optional_subject_relation
                    valid = direct and share.subject_id != "*"
                    if valid:
                        valid = get_user_model()._base_manager.filter(pk=share.subject_id).exists()
                    if share.relation == "requester":
                        valid = valid and str(requester_id) == share.subject_id
                    subject = str(SubjectRef.of(share.subject_type, share.subject_id, share.optional_subject_relation))
                    if not valid:
                        yield DisclosureChange("resolve legacy invitation", str(ref), subject, blocker=True)
                        continue
                    if share.relation == "responder" and share.subject_id not in holder_ids:
                        holder = SubjectRef.of("auth/user", share.subject_id)
                        if (
                            share.subject_id == str(requester_id)
                            or backend()
                            .check_access(
                                subject=holder,
                                action="manage",
                                resource=ref,
                            )
                            .allowed
                        ):
                            yield DisclosureChange("resolve incompatible responder", str(ref), subject, blocker=True)
                            continue
                        yield DisclosureChange("create shell", str(ref), subject)
                        if apply:
                            # Legacy admission may belong to a terminal round. Bulk insertion
                            # retains it without replaying today's admission lifecycle.
                            self.proposals._base_manager.bulk_create(
                                [
                                    self.proposals(round=round, responder_id=share.subject_id),
                                ]
                            )
                        holder_ids.add(share.subject_id)
                    yield DisclosureChange("delete legacy invitation", str(ref), subject)
                    if apply:
                        share.delete()
                disclosed_ids = {
                    str(row.responder_id)
                    for row in shells
                    if row.responder_id is not None and row.disclosed_at is not None
                }
                submitted_ids = {
                    str(row.responder_id)
                    for row in shells
                    if row.responder_id is not None and row.submitted_at is not None
                }
                for proposal in shells:
                    proposal_ref = to_object_ref(proposal)
                    ceremony = self.relationships.objects.filter(
                        resource_type=proposal_ref.resource_type,
                        resource_id=proposal_ref.resource_id,
                        subject_type="auth/user",
                        optional_subject_relation="",
                    )
                    for share in ceremony.order_by("pk").iterator(chunk_size=500):
                        owned = share.relation == "editor" and share.subject_id == str(proposal.responder_id)
                        owned |= (
                            share.relation == "reader"
                            and proposal.disclosed_at is not None
                            and share.subject_id in disclosed_ids
                        )
                        if owned:
                            yield DisclosureChange("delete ceremony share", str(proposal_ref), share.subject_id)
                            if apply:
                                share.delete()
                    if proposal.track_id is None:
                        continue
                    track_ref = to_object_ref(proposal.track)
                    shares = self.relationships.objects.filter(
                        resource_type=track_ref.resource_type,
                        resource_id=track_ref.resource_id,
                        subject_type="auth/user",
                        optional_subject_relation="",
                    ).order_by("pk")
                    for share in shares.iterator(chunk_size=500):
                        published = share.relation == "reader" and share.subject_id in holder_ids
                        ceremony_reader = share.relation == "reader" and share.subject_id in submitted_ids
                        editor = share.relation == "editor" and share.subject_id in {
                            str(round.facilitator_id),
                            str(proposal.responder_id),
                        }
                        if published and proposal.track_published_at is None:
                            yield DisclosureChange("stamp track publication", str(proposal_ref), share.subject_id)
                            if apply:
                                proposal.track_published_at = round.opened_at or timezone.now()
                                self.proposals._base_manager.filter(pk=proposal.pk).update(
                                    track_published_at=proposal.track_published_at,
                                )
                        if ceremony_reader or editor:
                            yield DisclosureChange("delete ceremony share", str(track_ref), share.subject_id)
                            if apply:
                                share.delete()

    def apply(self) -> list[DisclosureChange]:
        """Refuse ambiguous legacy seats before any write; apply atomically."""
        with transaction.atomic(), system_context(reason="proposals.disclosure.apply"):
            # Serialize with every round-owned lifecycle/admission verb.
            list(self.rounds.objects.lock_if_supported().order_by("pk").values_list("pk", flat=True))
            blockers = [change for change in self.changes() if change.blocker]
            if blockers:
                raise ValidationError(f"Resolve {len(blockers)} legacy requester or group invitations before applying.")
            return list(self.changes(apply=True))
