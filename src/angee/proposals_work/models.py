"""Compose proposal question routing with Work's native queue lifecycle."""

from __future__ import annotations

from typing import Any, ClassVar

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import models, router, transaction
from django_compose.models import Composable
from rebac import database_context, system_context, to_object_ref, to_subject_ref
from rebac.backends import backend
from rebac.database import write_alias


class RoundQuestionsQueue(Composable, extends="proposals.Round"):
    """Route new questions without moving questions already asked."""

    clarification_queue = models.ForeignKey(
        "work.Queue", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    hasura_filterable_fields: ClassVar[tuple[str, ...]] = ("clarification_queue",)
    hasura_insertable_fields: ClassVar[tuple[str, ...]] = ("clarification_queue",)
    hasura_updatable_fields: ClassVar[tuple[str, ...]] = ("clarification_queue",)

    class Meta:
        abstract = True

    @classmethod
    def setup_values(cls, *, clarification_queue: Any = None, **values: Any) -> dict[str, Any]:
        """Consume the native queue choice before admitting responders."""

        result = super().setup_values(**values)
        if clarification_queue is not None:
            queue_model = apps.get_model("work", "Queue")
            if not isinstance(clarification_queue, queue_model) or clarification_queue.pk is None:
                raise ValidationError({"clarification_queue": "Choose a saved queue."})
            if clarification_queue._state.db not in (None, write_alias(cls)):
                raise ValidationError({"clarification_queue": "Choose a queue from the round's database."})
            result["clarification_queue_id"] = clarification_queue.pk
        return result

    @classmethod
    def setup_complete_condition(cls, actor: Any) -> models.Q:
        """The bridge's setup is complete when question routing is configured."""

        return super().setup_complete_condition(actor) & models.Q(clarification_queue__isnull=False)

    def clean(self) -> None:
        """A round routes questions to a shared queue, never a personal one."""

        super().clean()
        if self.clarification_queue_id is not None:
            queue_model = apps.get_model("work", "Queue")
            with system_context(reason="proposals_work.round.queue_validation"):
                queue = (
                    queue_model._base_manager.using(self._state.db)
                    .get(pk=self.clarification_queue_id)
                )
            if queue.is_personal:
                raise ValidationError({"clarification_queue": "Choose a non-personal queue for round questions."})

    def route_clarification(self, task: Any, step: str) -> None:
        """Delegate stage selection to Work before insertion and after assignment."""

        super().route_clarification(task, step)
        if step == "asked" and self.clarification_queue_id is not None:
            task.queue_id = self.clarification_queue_id
        elif step == "passed":
            task.start()


class TaskTrackQueue(Composable, extends="projects.Task"):
    """Keep unpublished track work in a responder's or round manager's queue."""

    class Meta:
        abstract = True

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Validate the persisted placement inside the same atomic write.

        Work infers queues from stages, cycles and personal defaults before the
        row write. The current queue is the written value when that column is
        selected; otherwise Work's loaded snapshot is. The project snapshot
        likewise retains the project ID for partial saves. A rejected placement
        rolls the entire write back.
        """

        using = kwargs.get("using") or self._state.db or router.db_for_write(type(self), instance=self)
        fields = kwargs.get("update_fields")
        adding = self._state.adding
        project_before = self._save_snapshot.get("project_id", models.DEFERRED)
        queue_before = self._work_loaded_value("queue_id") if not adding and fields is None else None
        with database_context(using), transaction.atomic(using=using):
            super().save(*args, **kwargs)
            placement_written = (
                bool({"project", "project_id", "queue", "queue_id", "stage", "stage_id", "cycle", "cycle_id"}
                     .intersection(fields)) if fields is not None
                else self.project_id != project_before or self.queue_id != queue_before
            )
            if not adding and not placement_written:
                return
            project_id = self._save_snapshot.get("project_id", self.project_id)
            queue_written = adding or fields is None or bool({"queue", "queue_id"}.intersection(fields))
            queue_id = self.queue_id if queue_written else self._work_loaded_value("queue_id")
            with system_context(reason="proposals_work.task.track_queue"):
                if project_id is None or queue_id is None:
                    return
                proposal = (
                    apps.get_model("proposals", "Proposal")
                    ._base_manager.using(using)
                    .filter(track_id=project_id, track_published_at__isnull=True)
                    .select_related("round")
                    .first()
                )
                if proposal is None:
                    return
                queue_model = apps.get_model("work", "Queue")
                queue = (
                    queue_model._base_manager.using(using)
                    .select_related("owner")
                    .get(pk=queue_id)
                )
                owner = queue.owner
                if queue.is_personal and owner is not None:
                    if (
                        queue.owner_id == proposal.responder_id
                        or backend()
                        .check_access(
                            subject=to_subject_ref(owner), action="manage", resource=to_object_ref(proposal.round)
                        )
                        .allowed
                    ):
                        return
                raise ValidationError(
                    {"queue": "Unpublished track tasks require a responder or round manager personal queue."}
                )
