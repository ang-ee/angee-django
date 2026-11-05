"""Native workflow definitions owners."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any, ClassVar

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils.functional import cached_property
from rebac import database_context
from rebac.database import write_alias

from angee.base.fields.text import ModelLabelField
from angee.iam.models import ServiceUserMixin
from angee.iam.models.append_only import ProtectedAppendOnlyModel
from angee.iam.models.base import ProtectedDataModel
from angee.iam.models.ownership import OwnerMixin
from angee.provenance.contrib.angee.history import HistoryMixin
from angee.provenance.contrib.angee.managers import AngeeHistoryManager
from angee.resources.mixins import ResourceLoadMixin
from angee.workflows.definition import Definition
from angee.workflows.managers import (
    WorkflowManager,
)
from angee.workflows.querysets import WorkflowVersionQuerySet
from angee.workflows.resources import WorkflowDefinitionResource
from angee.workflows.triggers import TriggerGrantTarget

logger = logging.getLogger(__name__)


class Workflow(ServiceUserMixin, ResourceLoadMixin, OwnerMixin, HistoryMixin, ProtectedDataModel, concrete=True):
    """Editable identity and draft, pointing to one immutable published version.

    A workflow is a grant root with transferable ownership
    (:class:`~angee.iam.models.ownership.OwnerMixin`); ``created_by`` stays attribution.
    An install without a user-backed actor leaves it ownerless, for platform
    administrators to manage or transfer.
    """

    record_display_paths: ClassVar[tuple[str, ...]] = ("name",)

    resource_class = WorkflowDefinitionResource
    rebac_grantable = {"editor": "write", "viewer": "write", "starter": "write", "operator": "write"}
    sqid_prefix = "wfl_"
    service_user_prefix = "workflow"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        editable=False,
        related_name="workflow_principal",
    )
    key = models.SlugField(max_length=100, unique=True)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    subject_model = ModelLabelField(max_length=200, blank=True, default="")
    draft = models.JSONField(default=dict)
    draft_revision = models.PositiveIntegerField(default=0, editable=False)
    layout = models.JSONField(default=dict, blank=True)
    published = models.ForeignKey(
        "workflows.WorkflowVersion",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )

    objects = WorkflowManager()

    class Meta:
        """Django options for the composed workflow identity."""

        abstract = True
        rebac_resource_type = "workflows/workflow"

    def validate_subject(self, subject: Any) -> None:
        """Accept the declared model and its MTI children when a subject is required."""
        if self.subject_model and (subject is None or not isinstance(subject, apps.get_model(self.subject_model))):
            raise ValidationError("The workflow subject has the wrong model.")

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Keep the service principal's name synchronized with its workflow."""

        using = write_alias(type(self), using=kwargs.get("using") or self._state.db, instance=self)
        kwargs["using"] = using
        creating = self._state.adding
        update_fields = kwargs.get("update_fields")
        name_written = update_fields is None or "name" in update_fields
        previous_name = None
        if not creating and name_written:
            previous_name = (
                type(self)._base_manager.using(using).filter(pk=self.pk).values_list("name", flat=True).first()
            )
        with database_context(using), transaction.atomic(using=using):
            super().save(*args, **kwargs)
            if creating or (name_written and previous_name != self.name):
                self.sync_service_user()

    def __str__(self) -> str:
        """Return the authored workflow name."""

        return self.name


WorkflowVersionManager = AngeeHistoryManager.from_queryset(WorkflowVersionQuerySet)


class WorkflowVersion(ProtectedAppendOnlyModel, ProtectedDataModel, concrete=True):
    """Immutable normalized graph document selected when a run starts."""

    record_display_paths: ClassVar[tuple[str, ...]] = ("number",)

    sqid_prefix = "wfv_"

    workflow = models.ForeignKey("workflows.Workflow", on_delete=models.PROTECT, related_name="versions")
    number = models.PositiveIntegerField()
    document = models.JSONField(default=dict)
    content_hash = models.CharField(max_length=64)
    published_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True)

    objects = WorkflowVersionManager()

    def __str__(self) -> str:
        """Identify the published version within its workflow."""
        return f"Version {self.number}"

    class Meta:
        """Django options for immutable workflow snapshots."""

        abstract = True
        rebac_resource_type = "workflows/version"
        constraints = [
            models.UniqueConstraint(fields=("workflow", "number"), name="workflows_version_number_unique"),
        ]

    @cached_property
    def definition(self) -> Definition:
        """Parse the immutable document once for this model instance."""

        return Definition.model_validate(self.document)

    def require_publisher_access(self, targets: Iterable[TriggerGrantTarget]) -> None:
        """Recheck human publication authority; system-installed versions are trusted."""
        if self.published_by_id is not None:
            for target in targets:
                target.require_publisher_access(self.published_by, self)
