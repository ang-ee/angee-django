"""Native import-export adapter for whole workflow graph documents."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from import_export import fields, widgets

from angee.resources.loader import AngeeResource


class WorkflowDefinitionResource(AngeeResource):
    """Delegate declaration persistence to the workflow's save/publish owner."""

    publish = fields.Field(column_name="publish", readonly=True, default=True, widget=widgets.BooleanWidget())

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
