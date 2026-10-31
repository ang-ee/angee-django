"""Request factories and message capture orchestration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import models, transaction
from rebac import PermissionDenied, current_actor, system_context

from angee.base.exceptions import DomainError
from angee.base.permissions import actor_user_id
from angee.intake.choices import NeedImportance
from angee.intake.querysets import NeedQuerySet
from angee.provenance.context import changeset, changeset_model, require_changeset
from angee.provenance.contrib.angee.managers import AngeeHistoryManager

if TYPE_CHECKING:
    from angee.intake.models import Need
    from angee.messaging.models import Message
    from angee.projects.models import Project, Task
    from angee.work.models import Queue

logger = logging.getLogger(__name__)


_NeedManagerBase = AngeeHistoryManager.from_queryset(NeedQuerySet)


class NeedManager[NeedT: Need](_NeedManagerBase[NeedT]):
    """Own manual request and message capture through the target owners."""

    TARGET_FIELDS = {
        "projects.project": "project",
        "projects.task": "task",
    }

    def file_task(
        self,
        *,
        queue: Any,
        title: str,
        body: str,
        party: Any,
        idempotency_key: str,
        due_date: Any = None,
        estimate: float | None = None,
        importance: str = "normal",
    ) -> Any:
        """File an actor-owned task and its need atomically, replaying the whole request."""

        if not queue.has_access("read"):
            raise PermissionDenied("Queue read access is required to select a task container.")
        if not party.has_access("read"):
            raise PermissionDenied("Party read access is required to file a request.")
        task_model = apps.get_model("projects", "Task")
        actor = current_actor()
        scope = actor_user_id(actor)
        if scope is None:
            raise PermissionDenied("Filing a task requires a user actor.")
        values = dict(queue_id=queue.pk, title=title, note=body, due_date=due_date, estimate=estimate)
        claim_values = {"task": values, "party": party, "importance": importance}

        with changeset(fresh=True), transaction.atomic():
            created = changeset_model().objects.claim_created(
                f"intake.file-task:{scope}",
                idempotency_key,
                values=claim_values,
                model=task_model,
                unavailable=ValidationError(
                    {"idempotency_key": "The claimed task is unavailable."},
                ),
            )
            if created is not None:
                return task_model.objects.with_actor(actor).get(pk=created.pk)
            task = task_model.objects.with_actor(actor).create(**values, created_by_id=scope)
            self.capture(target=task, party=party, body=body, importance=importance)
            require_changeset().record_created(task)
            return task

    def target_model(self, model_label: str) -> type[Project] | type[Task]:
        """Return the declared relation target for one allowed need target label."""

        normalized = str(model_label).strip().lower()
        if normalized not in self.TARGET_FIELDS:
            raise ValidationError({"target": "Needs may target only projects or tasks."})
        field = self.TARGET_FIELDS[normalized]
        return self.model.task.field.related_model if field == "task" else self.model.project.field.related_model

    @classmethod
    def target_field(cls, target: models.Model) -> str:
        """Return the stored target field owned by ``target``'s model."""

        try:
            return cls.TARGET_FIELDS[target._meta.label_lower]
        except KeyError as error:
            raise ValidationError({"target": "Needs may target only projects or tasks."}) from error

    def capture(
        self,
        *,
        target: Project | Task,
        body: str,
        party: models.Model | None = None,
        importance: str = "normal",
    ) -> NeedT:
        """Capture one exact manual request onto ``target`` under its write lock.

        The authored action has no separate idempotency token, so its replay
        identity is the normalized manual payload: target, body, party, and
        importance. A caller intentionally recording distinct identical evidence
        can edit the wording; ordinary action retries converge on the existing row.
        PostgreSQL makes that convergence exact with a target-row lock; SQLite
        provides the single-writer boundary through database-level serialization.
        """

        if target.pk is None:
            raise ValidationError({"target": "A saved project or task is required."})
        body = str(body or "").strip()
        if not body:
            raise ValidationError({"body": "A manual need requires a body."})
        try:
            importance_value = NeedImportance(getattr(importance, "value", importance))
        except ValueError as error:
            raise ValidationError({"importance": "Choose normal or important."}) from error

        target_field = self.target_field(target)
        target_filter = {f"{target_field}_id": target.pk}
        actor = current_actor()
        with transaction.atomic():
            with system_context(reason="intake.need.capture.lookup"):
                locked_target = type(target).objects.sudo(reason="intake.need.capture.target").locked_get(pk=target.pk)
                existing = (
                    self.model._base_manager.filter(
                        **target_filter,
                        party_id=None if party is None else party.pk,
                        body=body,
                        importance=importance_value,
                        source_message__isnull=True,
                    )
                    .order_by("pk")
                    .first()
                )
            if existing is not None:
                return existing.with_actor(actor)

            need = self.model(
                **{target_field: locked_target},
                party=party,
                body=body,
                importance=importance_value,
            )
            need.full_clean(validate_unique=False, validate_constraints=False)
            if party is not None:
                need.validate_party_assignment()
            # ``Need.save()`` deliberately persists an authorized party assignment
            # as a second, elevated write after the actor-scoped insert.  Keep the
            # actor on the instance rather than wrapping the whole save in the
            # queryset insertion scope: that scope would also pin the nested write
            # and defeat the instance-local elevation.
            need.with_actor(actor).save(force_insert=True, using=self.db)
            return need

    def capture_from_message(
        self,
        message: Message,
        *,
        queue: Queue,
        values: dict[str, Any] | None = None,
    ) -> NeedT:
        """Capture one message into triage; its clean replay no-op is SELECT-FOR-UPDATE-backed."""

        if message.pk is None or queue.pk is None:
            raise ValidationError("A saved message and intake queue are required.")
        message_model = type(message)
        with system_context(reason="intake.need.capture_message"), transaction.atomic():
            locked_message = (
                message_model.objects.sudo(reason="intake.need.capture_message.message")
                .lock_if_supported()
                .select_related("sender", "thread__title")
                .get(pk=message.pk)
            )
            existing = (
                self.model._base_manager.select_related("task").filter(source_message_id=locked_message.pk).first()
            )
            if existing is not None:
                return existing

            channel = locked_message.transport_channel(reason="intake.capture.configuration")
            if channel is None:
                raise ValidationError("Message capture requires its intake channel.")
            mapped = dict(channel.mapped_task_values(locked_message) if values is None else values)
            claimed_name = mapped.pop("claimed_name", "")
            submission = locked_message.webform_submission()
            task = (
                apps.get_model("projects", "Task")
                .objects.db_manager(self.db)
                .capture_triage(
                    queue=queue,
                    title=mapped.pop("title", self._message_title(locked_message)),
                    note=mapped.pop("note", str(locked_message.preview or "")),
                    created_by_id=locked_message.created_by_id,
                    values=mapped,
                )
            )
            need = self.model(
                task=task,
                claimed_name=claimed_name,
                claimed_email=(submission.unverified_submitter_email or "") if submission else "",
                source_message=locked_message,
                importance=NeedImportance.NORMAL,
                created_by_id=locked_message.created_by_id,
                updated_by_id=locked_message.updated_by_id,
            )
            need.full_clean(validate_unique=False, validate_constraints=False)
            need.sudo(reason="intake.need.capture_message.create").save(_access_decision=True)
            attachment_model = apps.get_model("messaging", "ThreadAttachment")
            attachment_model.objects.bind_source_thread(task, locked_message.thread)
            try:
                # Identity refusals roll back linking, never the captured evidence.
                with transaction.atomic():
                    need.party_id = self._resolved_sender_party_id(locked_message)
                    if need.party_id is None and need.claimed_email and channel.intake_requester_domains:
                        need._link_requester(
                            allow_create=need.claimed_email.rsplit("@", 1)[-1].lower()
                            in channel.intake_requester_domains,
                            system_reason=f"intake.requester_domain:{channel.slug}",
                        )
                    if need.party_id is not None:
                        need.save(_access_decision=True, update_fields=("party",))
            except DomainError, ValidationError, PermissionDenied:
                logger.info("Requester linking was refused for need %s; capture is retained.", need.pk)
                need.refresh_from_db()
            # Capture owns the initial question only after identity hydration;
            # no transient actorless question needs to be withdrawn.
            need.access_decision = need._new_access_decision()
            self.model.system_queryset().filter(pk=need.pk).update(access_decision=need.access_decision)
            return need

    @staticmethod
    def _message_title(message: Message) -> str:
        """Return a stable task title from the message preview or thread title."""

        preview = str(message.preview or "").strip()
        if preview:
            return preview
        thread = getattr(message, "thread", None)
        title = getattr(getattr(thread, "title", None), "text", "")
        return str(title or "Captured message")

    @staticmethod
    def _resolved_sender_party_id(message: Message) -> Any | None:
        """Resolve the sender through parties' matching owner and return its party id."""

        sender = message.sender
        if sender is None:
            return None
        if sender.party_id is None:
            party_model = apps.get_model("parties", "Party")
            party_model.objects.suggest_for(sender)
            sender.refresh_from_db(fields=("party", "party_link_confirmed"))
        return sender.party_id if sender.party_link_confirmed else None


