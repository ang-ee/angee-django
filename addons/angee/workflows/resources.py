"""Native import-export adapter for whole workflow graph documents."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from import_export import fields, widgets
from pydantic import ValidationError as PydanticValidationError

from angee.resources.exceptions import ResourceLoadError
from angee.resources.kinds import ResourceEntryKind
from angee.resources.loader import AngeeResource
from angee.workflows.definition import SourcedWorkflowExtension, WorkflowExtension

WORKFLOW_EXTENSION_KIND = "workflows.workflowextension"
"""Install resource kind for additive workflow graph fragments."""


class WorkflowExtensionResourceKind(ResourceEntryKind):
    """Parse composer-owned workflow extension declarations."""

    key = WORKFLOW_EXTENSION_KIND

    @classmethod
    def read(cls, entry: Any) -> tuple[SourcedWorkflowExtension, ...]:
        """Pair typed rows with the addon order already carried by the entry."""

        declarations: list[SourcedWorkflowExtension] = []
        for index, row in enumerate(entry.read_declarations(), start=1):
            try:
                extension = WorkflowExtension.model_validate(row)
                extension.fragment()
            except (PydanticValidationError, ValueError) as error:
                raise ResourceLoadError(f"{entry.display} row {index}: {error}") from error
            declarations.append(SourcedWorkflowExtension(entry.addon.name, extension))
        return tuple(declarations)

    @classmethod
    def after_load(
        cls,
        declarations: Any,
        *,
        loaded_groups: Any,
        dry_run: bool,
    ) -> None:
        """Refuse extensions whose base definition was absent from this load."""

        del dry_run
        loaded: set[str] = set()
        for group, resource in loaded_groups:
            if not isinstance(resource, WorkflowDefinitionResource):
                continue
            for xref in group.dataset["_xref"]:
                instance = resource.instance_for_xref(xref)
                if instance is not None:
                    loaded.add(instance.key)
        missing = sorted({item.extension.extends for item in declarations} - loaded)
        if missing:
            raise ResourceLoadError(
                "Workflow extensions require their base definition in the same resource load; missing: "
                + ", ".join(missing)
            )


class WorkflowDefinitionResource(AngeeResource):
    """Delegate declaration persistence to the workflow's save/publish owner."""

    publish = fields.Field(column_name="publish", readonly=True, default=True, widget=widgets.BooleanWidget())

    def _extensions_for(self, key: str) -> tuple[SourcedWorkflowExtension, ...]:
        """Return this load's contributions, retaining the published set for a narrow load."""

        if self.kind_declarations is None:
            workflow = self._meta.model.system_queryset().filter(key=key).select_related("published").first()
            return (
                workflow.published.definition.retained_extensions()
                if workflow is not None and workflow.published_id is not None
                else ()
            )

        return tuple(
            item
            for item in self.kind_declarations.get(WORKFLOW_EXTENSION_KIND, ())
            if isinstance(item, SourcedWorkflowExtension) and item.extension.extends == key
        )

    def _row_content_hash(self, row: Mapping[str, Any]) -> str:
        """Include matching contribution declarations in the base row ledger hash."""

        payload = dict(row)
        key = payload.get("key")
        extensions = self._extensions_for(key) if isinstance(key, str) else ()
        payload["_workflow_extensions"] = [item.fingerprint() for item in extensions]
        return super()._row_content_hash(payload)

    def save_instance(self, instance: Any, is_create: bool, row: Mapping[str, Any], **kwargs: Any) -> None:
        """Install the cleaned document and retain the native import ledger hooks."""

        del is_create
        self.before_save_instance(instance, row, **kwargs)
        installed = type(instance).objects.install_definition(
            key=instance.key,
            name=instance.name,
            description=instance.description,
            subject_model=instance.subject_model,
            draft=instance.draft,
            layout=instance.layout,
            extensions=self._extensions_for(instance.key),
            publish=(
                self.fields["publish"].clean(row) if "publish" in row else self.fields["publish"].default
            ),
            actor=kwargs.get("actor"),
        )
        for field in instance._meta.concrete_fields:
            setattr(instance, field.attname, getattr(installed, field.attname))
        instance._state.adding = False
        instance._state.db = installed._state.db
        self.after_save_instance(instance, row, **kwargs)


class TriggerResource(AngeeResource):
    """Install declarations disabled; enabling grants the workflow principal."""

    def before_import_row(self, row: dict[str, Any], **kwargs: Any) -> None:
        """Treat activation as operator state, never declaration input."""
        row.update(enabled=False, disabled_reason="")
        super().before_import_row(row, **kwargs)

    def before_save_instance(self, instance: Any, row: Mapping[str, Any], **kwargs: Any) -> None:
        """Clear activation even when replacing a previously enabled declaration."""
        instance.enabled = False
        instance.disabled_reason = ""
        super().before_save_instance(instance, row, **kwargs)
