"""Native round factories and replay-safe proposal message capture."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from contextlib import nullcontext
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any, cast

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.utils.dateparse import parse_date
from rebac import PermissionDenied, actor_context, check_new, current_actor, database_context, system_context
from rebac.actors import to_subject_ref
from rebac.database import write_alias
from rebac.errors import MissingActorError
from rebac.resources import model_resource_type

from angee.base.fields.ordering import FractionalRankField
from angee.base.managers import AngeeManager
from angee.base.serialization import STRINGIFIED_JSON, canonical_json_sha256
from angee.base.validators import validate_value
from angee.proposals.choices import ProposalState, RoundStatus
from angee.proposals.inputs import RoundTemplate
from angee.proposals.querysets import RoundQuerySet, _RoundBaseQuerySet

if TYPE_CHECKING:
    from angee.iam.models import User
    from angee.messaging.models import Message
    from angee.parties.models import Party
    from angee.projects.models import Project, Task
    from angee.proposals.models import Proposal, Round
    from angee.spaces.models import Group


_RoundBaseManager = models.Manager.from_queryset(_RoundBaseQuerySet)

_RoundManagerBase = AngeeManager.from_queryset(RoundQuerySet)


class RoundManager[T: Round](_RoundManagerBase[T]):
    """Provision rounds by composing the native topic and admission owners."""

    def provision(
        self,
        target: Project | Task,
        template: object,
        facilitator: User,
        responders: Iterable[User],
        team: Group | None = None,
        *,
        requester_party: Party | None = None,
        configuration: Mapping[str, Any] | None = None,
    ) -> T:
        """Resume a round with the same target, name and declared settings."""

        round_template: RoundTemplate = validate_value(RoundTemplate, template, field="template")
        target_field = {"projects.project": "project", "projects.task": "task"}.get(target._meta.label_lower)
        if target_field is None:
            raise ValidationError({"target": "Choose a project or task."})
        actor = current_actor()
        resource_type = model_resource_type(self.model)
        if actor is None:
            raise MissingActorError(f"Creating {resource_type or self.model._meta.label_lower} requires an actor.")
        if resource_type is None or not check_new(
            subject=actor,
            action="create",
            resource_type=resource_type,
            relationships={target_field: (to_subject_ref(target),)},
        ).allowed:
            raise PermissionDenied(f"Denied: {actor} cannot create {resource_type or self.model._meta.label_lower}")
        with transaction.atomic():
            type(target).system_queryset(lock=("self",)).get(pk=target.pk)
            candidate = self.model(**{target_field: target}, facilitator=facilitator, team=team)
            project = candidate.target_project()
            values = round_template.round_values(project)
            setup_values = self.model.setup_values(**(configuration or {}))
            if requester_party is not None:
                setup_values["requester_party_id"] = requester_party.pk
            rows = self.sudo(reason="proposals.round.provision").filter(
                **{target_field: target},
                name=round_template.name,
                closed_at__isnull=True,
            )
            round = rows.first()
            if round is None:
                round = self.model(
                    **{target_field: target},
                    facilitator=facilitator,
                    team=team,
                    **values,
                    **setup_values,
                )
                round.sudo(reason="proposals.round.provision").save()
            elif not round.with_actor(actor).has_access("write"):
                raise PermissionDenied("Round write access is required to resume provisioning.")
            elif any(getattr(round, name) != value for name, value in values.items()) or (
                round.facilitator_id != facilitator.pk or round.team_id != (team.pk if team else None)
            ):
                raise ValidationError({"template": "The existing round has different settings."})
            else:
                changed = []
                for name, value in setup_values.items():
                    previous = getattr(round, name)
                    if previous == value:
                        continue
                    if previous is not None:
                        raise ValidationError({"round": "The existing round has different setup choices."})
                    setattr(round, name, value)
                    changed.append(name)
                if changed:
                    round.save(update_fields=(*changed, "updated_at"))
            topic_model = apps.get_model("proposals", "Topic")
            with system_context(reason="proposals.round.provision.topics"):
                topics = {topic.key: topic for topic in topic_model._base_manager.filter(round=round)}
                if set(topics) - {topic.key for topic in round_template.topics}:
                    raise ValidationError({"template": "The existing round has different topic keys."})
                rank = None
                for topic in round_template.topics:
                    rank = FractionalRankField.get_append_rank(rank)
                    values = dict(name=topic.name, hint=topic.hint, sort_order=rank)
                    existing = topics.get(topic.key)
                    if existing is None:
                        topic_model.objects.create(round=round, key=topic.key, **values)
                    elif any(getattr(existing, key) != value for key, value in values.items()):
                        raise ValidationError({"template": "The existing topic differs from the template."})
            round.sudo(reason="proposals.round.provision.admission")
            for responder in responders:
                round.admit(responder, track=round_template.tracks)
        return round.with_actor(actor)

    def for_track(self, project_id: Any, *, lock: bool = False) -> T | None:
        """Return the round whose admitted shell owns ``project_id`` as its track, else ``None``.

        Track items keep the round's holder ceiling and manager-only sharing; an
        ordinary project or a question on the round's target project has no round here.
        """

        if project_id is None:
            return None
        return (
            self.model.system_queryset(lock=("self",) if lock else None).using(self._db)
            .filter(proposals__track_id=project_id).order_by("pk").first()
        )

class ProposalManager[T: Proposal](AngeeManager[T]):
    """Own replay-safe message capture into Proposal and Answer rows."""

    CAPTURE_PARSER_VERSION = "proposals.capture.v1"
    CAPTURE_FIELDS = (
        "cost",
        "currency",
        "staffing",
        "timeframe_start",
        "timeframe_end",
        "confidence",
        "valid_until",
    )

    def capture_from_message(
        self,
        message: Message,
        round: Round,
        party: Party | None = None,
    ) -> T:
        """Capture on the selected database after round write and related read admission.

        Explicit parties require read access; an omitted party uses the existing
        sender-matching owner. A trusted system context remains supported.
        """

        if message.pk is None or round.pk is None:
            raise ValidationError("A saved message and round are required for capture.")
        using = write_alias(self.model, using=self._db or round._state.db, instance=round)
        if any(row._state.db not in (None, using) for row in (round, message, *(() if party is None else (party,)))):
            raise ValidationError("Capture references must belong to the selected database.")
        owner = self.db_manager(using)
        with database_context(using):
            actor = round.require_access("write")
            message.require_access("read", actor=actor)
            if party is not None:
                party.require_access("read", actor=actor)
            with transaction.atomic(using=using), actor_context(actor) if actor is not None else nullcontext():
                locked_round = (
                    type(round).objects.db_manager(using)
                    .sudo(reason="proposals.capture.round").lock_if_supported().get(pk=round.pk)
                )
                if locked_round.status != RoundStatus.COLLECTING:
                    raise ValidationError({"round": "Replies cannot be captured after opening."})
                locked_message = (
                    type(message).objects.db_manager(using)
                    .sudo(reason="proposals.capture.message").lock_if_supported().get(pk=message.pk)
                )
                resolved_party = party or owner._resolved_sender_party(locked_message)
                if resolved_party is None:
                    raise ValidationError({"party": "Capture requires a party or a resolved sender party."})
                interpretation = owner._interpret(locked_message, locked_round)
                payload_hash = owner._payload_hash(locked_message)
                proposal = owner._locked_capture_proposal(
                    message=locked_message, round=locked_round, party=resolved_party,
                )
                if proposal.state != ProposalState.DRAFT:
                    if not (
                        proposal.state == ProposalState.SUBMITTED
                        and proposal.capture_payload_hash == payload_hash
                        and proposal.capture_parser_version == owner.CAPTURE_PARSER_VERSION
                    ):
                        raise ValidationError("Captured proposal does not match the replayed interpretation.")
                else:
                    owner._apply_interpretation(
                        proposal, message=locked_message, interpretation=interpretation, payload_hash=payload_hash,
                    )
                    proposal._submit_locked(
                        fallback_user_id=locked_message.created_by_id or locked_round.facilitator_id,
                    )
        if actor is not None:
            proposal.with_actor(actor)
        else:
            proposal.unsudo()
        return proposal

    def _locked_capture_proposal(
        self,
        *,
        message: Message,
        round: Round,
        party: Party,
    ) -> T:
        """Resolve an existing capture shell or create/reload its unique row."""

        candidates = list(
            self.sudo(reason="proposals.capture.lookup")
            .lock_if_supported()
            .filter(models.Q(source_message_id=message.pk) | models.Q(round_id=round.pk, party_id=party.pk))
            .order_by("pk")
        )
        if len(candidates) > 1:
            raise ValidationError("Message and party resolve to different proposal shells.")
        if candidates:
            proposal = candidates[0]
            if proposal.round_id != round.pk or proposal.party_id != party.pk:
                raise ValidationError("The captured message is already bound to another proposal.")
            if proposal.source_message_id not in (None, message.pk):
                raise ValidationError("The party's proposal already has a different source message.")
            return proposal

        proposal = self.model(round=round, party=party, source_message=message)
        proposal.full_clean(validate_unique=False, validate_constraints=False)
        try:
            with transaction.atomic(using=self.db):
                proposal.sudo(reason="proposals.capture.create").save(using=self.db)
        except IntegrityError:
            concurrent = (
                self.sudo(reason="proposals.capture.concurrent_reload")
                .lock_if_supported()
                .filter(models.Q(source_message_id=message.pk) | models.Q(round_id=round.pk, party_id=party.pk))
                .order_by("pk")
                .first()
            )
            if concurrent is None:
                raise
            proposal = concurrent
            if proposal.round_id != round.pk or proposal.party_id != party.pk:
                raise ValidationError("A concurrent capture claimed the message for another proposal.")
        return proposal

    def _interpret(self, message: Message, round: Round) -> dict[str, Any]:
        """Return the deterministic v1 interpretation envelope for one Message."""

        metadata = message.metadata if isinstance(message.metadata, Mapping) else {}
        raw = metadata.get("proposal_capture", {})
        if raw is None:
            raw = {}
        if not isinstance(raw, Mapping):
            raise ValidationError({"message": "proposal_capture metadata must be an object."})
        allowed = {*self.CAPTURE_FIELDS, "answers"}
        unknown = sorted(set(raw) - allowed)
        if unknown:
            raise ValidationError({"message": f"Unknown proposal capture fields: {', '.join(unknown)}."})

        answers_raw = raw.get("answers", {})
        if not isinstance(answers_raw, Mapping):
            raise ValidationError({"message": "proposal_capture.answers must be an object."})
        topics = {
            topic.key.casefold(): topic
            for topic in apps.get_model("proposals", "Topic")
            ._base_manager.using(self.db).filter(round_id=round.pk)
            .order_by("sort_order", "pk")
        }
        answers: dict[Any, str] = {}
        for supplied_key, body in answers_raw.items():
            key = str(supplied_key).strip().casefold()
            topic = topics.get(key)
            if topic is None:
                raise ValidationError({"message": f"Unknown round topic {supplied_key!r}."})
            answers[topic.pk] = str(body or "").strip()
        if not answers and str(message.preview or "").strip() and topics:
            first = next(iter(topics.values()))
            answers[first.pk] = str(message.preview).strip()

        fields = {name: raw[name] for name in self.CAPTURE_FIELDS if name in raw}
        if not answers and not fields:
            raise ValidationError({"message": "The reply contains no proposal fields or answers."})
        return {"answers": answers, "fields": fields}

    def _apply_interpretation(
        self,
        proposal: T,
        *,
        message: Message,
        interpretation: Mapping[str, Any],
        payload_hash: str,
    ) -> None:
        """Upsert interpreted fields and Answers before the final submit edge."""

        fields = dict(cast(Mapping[str, Any], interpretation["fields"]))
        currency_value = fields.pop("currency", None)
        if "cost" in fields:
            fields["cost"] = Decimal(str(fields["cost"])) if fields["cost"] not in (None, "") else None
        if currency_value not in (None, ""):
            currency_model = apps.get_model("money", "Currency")
            fields["currency"] = (
                currency_model._base_manager.using(self.db).get(code__iexact=str(currency_value).strip())
            )
        elif "cost" in fields:
            fields["currency"] = None
        for name in ("timeframe_start", "timeframe_end", "valid_until"):
            if name in fields and fields[name] not in (None, ""):
                parsed = fields[name] if isinstance(fields[name], date) else parse_date(str(fields[name]))
                if parsed is None:
                    raise ValidationError({name: "Use an ISO date."})
                fields[name] = parsed
        for name, value in fields.items():
            setattr(proposal, name, value)
        if proposal.source_message_id is None:
            proposal.source_message = message
            proposal.allow_immutable_save("source_message_id")
        proposal.capture_payload_hash = payload_hash
        proposal.capture_parser_version = self.CAPTURE_PARSER_VERSION
        proposal.full_clean(validate_unique=False, validate_constraints=False)
        proposal.save(
            using=self.db,
            update_fields=(
                *fields,
                "source_message",
                "capture_payload_hash",
                "capture_parser_version",
                "updated_at",
            ),
        )

        answer_model = apps.get_model("proposals", "Answer")
        topics = apps.get_model("proposals", "Topic")._base_manager.using(self.db).in_bulk(interpretation["answers"])
        for topic_id, body in cast(Mapping[Any, str], interpretation["answers"]).items():
            answer = (
                answer_model._base_manager.using(self.db).filter(proposal_id=proposal.pk, topic_id=topic_id).first()
            )
            if answer is None:
                answer = answer_model(proposal=proposal, topic=topics[topic_id], body=body)
            else:
                answer.body = body
            answer.full_clean(validate_unique=False, validate_constraints=False)
            answer.sudo(reason="proposals.capture.answer").save(using=self.db)

    def _resolved_sender_party(self, message: Message) -> Party | None:
        """Resolve the sender through the parties-owned handle matching seam."""

        if message.sender_id is None:
            return None
        handle = message.sender
        assert handle is not None
        if handle.party_id is None:
            apps.get_model("parties", "Party").objects.db_manager(self.db).suggest_for(handle)
            # Matching may assign the party through another Handle instance.
            handle.refresh_from_db(fields=["party"])
        return handle.party

    def _payload_hash(self, message: Message) -> str:
        """Hash the portable source payload interpreted by this parser version."""

        payload = {
            "parser_version": self.CAPTURE_PARSER_VERSION,
            "preview": str(message.preview or ""),
            "metadata": message.metadata if isinstance(message.metadata, Mapping) else {},
        }
        return canonical_json_sha256(chunks=(STRINGIFIED_JSON.encode(payload),))
