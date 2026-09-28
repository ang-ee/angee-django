"""Compose proposal question routing with Work's native queue lifecycle."""

from __future__ import annotations

from typing import Any, ClassVar

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import models, transaction
from rebac import system_context, to_object_ref, to_subject_ref
from rebac.backends import backend


class RoundQuestionsQueue(models.Model):
    """Route new questions without moving questions already asked."""

    extends = "proposals.Round"
    clarification_queue = models.ForeignKey(
        "work.Queue", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    hasura_filterable_fields: ClassVar[tuple[str, ...]] = ("clarification_queue",)
    hasura_insertable_fields: ClassVar[tuple[str, ...]] = ("clarification_queue",)
    hasura_updatable_fields: ClassVar[tuple[str, ...]] = ("clarification_queue",)

    class Meta:
        abstract = True

    def clean(self) -> None:
        """A round routes questions to a shared queue, never a personal one."""

        super().clean()
        if self.clarification_queue_id is not None:
            queue_model = apps.get_model("work", "Queue")
            with system_context(reason="proposals_work.round.queue_validation"):
                slug = queue_model._base_manager.values_list("slug", flat=True).get(pk=self.clarification_queue_id)
            if slug.startswith(queue_model.objects.PERSONAL_SLUG_PREFIX):
                raise ValidationError({"clarification_queue": "Choose a non-personal queue for round questions."})

    def route_clarification(self, task: Any, step: str) -> None:
        """Delegate stage selection to Work before insertion and after assignment."""

        super().route_clarification(task, step)
        if step == "asked" and self.clarification_queue_id is not None:
            task.queue_id = self.clarification_queue_id
        elif step == "passed":
            task.start()


class TaskTrackQueue(models.Model):
    """Keep unpublished track work in a responder's or round manager's queue."""

    extends = "projects.Task"

    class Meta:
        abstract = True

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Validate the persisted placement inside the same atomic write.

        Work infers queues from stages, cycles and personal defaults during save.
        Reading its final columns avoids duplicating that policy and respects
        partial/deferred saves. A rejected placement rolls the entire write back.
        """

        with transaction.atomic():
            super().save(*args, **kwargs)
            if self._state.adding:
                return
            with system_context(reason="proposals_work.task.track_queue"):
                placement = type(self)._base_manager.values("project_id", "queue_id").get(pk=self.pk)
                if placement["project_id"] is None or placement["queue_id"] is None:
                    return
                proposal = (
                    apps.get_model("proposals", "Proposal")
                    ._base_manager.filter(track_id=placement["project_id"], track_published_at__isnull=True)
                    .select_related("round")
                    .first()
                )
                if proposal is None:
                    return
                queue_model = apps.get_model("work", "Queue")
                queue = queue_model._base_manager.select_related("owner").get(pk=placement["queue_id"])
                if queue.owner_id is not None and queue.slug == queue_model.objects.personal_slug(queue.owner):
                    if queue.owner_id == proposal.responder_id or backend().check_access(
                        subject=to_subject_ref(queue.owner), action="manage", resource=to_object_ref(proposal.round)
                    ).allowed:
                        return
                raise ValidationError(
                    {"queue": "Unpublished track tasks require a responder or round manager personal queue."}
                )
