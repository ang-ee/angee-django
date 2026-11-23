"""Native import-export adapter that declares a published web form as one resource row.

A row in a ``<NNN>_webforms.form.yaml`` resource file names the form's own fields
and two columns of its own: ``version``, the content of the version the form
serves (``FormVersion.CONTENT_FIELDS``), and ``aliases``, the slugs it answers at.
The loader owns the row's identity (its ledger xref), its content hash and its
one transaction; this adapter saves the form natively and hands the version and
the addresses to the form's own verbs (:meth:`~angee.webforms.models.Form.serve`,
:meth:`~angee.webforms.models.Form.ensure_aliases`). The web forms README states
what a load, a reload and a changed row do.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import nullcontext
from typing import Any

from django.core.exceptions import ValidationError
from import_export import fields
from rebac import actor_context

from angee.resources.exceptions import ResourceLoadError
from angee.resources.loader import AngeeResource
from angee.resources.widgets import NativeJSONWidget


class FormResource(AngeeResource):
    """Install a declared form: its fields natively, then the version it serves and its addresses.

    Every row states its ``version``. ``target_config`` names records by xref
    where its target's ``config_model`` declares a relation
    (``json_schema_extra={"relation": {"resource": …}}``); each resolves to its
    record's public id when the row is applied, so the form never learns what a
    target configures. ``retention_days`` is the deployment's once the form
    exists: a file that declares it lists it under the entry's ``create_only``.
    """

    version = fields.Field(column_name="version", readonly=True, widget=NativeJSONWidget())
    aliases = fields.Field(column_name="aliases", readonly=True, widget=NativeJSONWidget())

    def before_import(self, dataset: Any, **kwargs: Any) -> None:
        """Refuse a declared retention that a changed row would apply again over the deployment's own."""

        super().before_import(dataset, **kwargs)
        if "retention_days" in (dataset.headers or ()) and "retention_days" not in self.entry.create_only:
            raise ResourceLoadError(
                f"{self.entry.display}: retention_days is the deployment's once a form exists, since a changed "
                "retention removes responses; list it under the entry's create_only."
            )

    def before_import_row(self, row: dict[str, Any], **kwargs: Any) -> None:
        """Refuse a row that states no version, whether it would make the form or change it."""

        super().before_import_row(row, **kwargs)
        if "version" not in row:
            raise ValidationError({"version": "Declare version: a declared form states the version it serves."})

    def import_instance(self, instance: Any, row: Mapping[str, Any], **kwargs: Any) -> None:
        """Resolve the record xrefs of ``target_config`` once the row's columns are on the form.

        They resolve as the row's relation columns do: through the ledger, in
        this load's addon selection and its system context
        (docs/backend/guidelines.md#resource-loading). Resolving names a record
        and grants nothing; the row's named actor is asked where the row
        persists (:meth:`save_instance`), per that guide's resource load hooks.
        """

        super().import_instance(instance, row, **kwargs)
        if "target_config" not in row or self.is_unchanged(row):
            return
        schema = instance.get_target_class_impl().config_json_schema()
        if schema is None:
            return
        try:
            instance.target_config = self.ledger_model.objects.resolve_config_references(
                instance.target_config, schema=schema, addon_aliases=self.addon_aliases,
            )
        except (ValueError, LookupError) as error:
            raise ValidationError({"target_config": str(error)}) from error

    def validate_instance(
        self, instance: Any, import_validation_errors: Any = None, validate_unique: bool = True,
    ) -> None:
        """Report a ``target_config`` reference that did not resolve alone: the target would only restate it."""

        if import_validation_errors and "target_config" in import_validation_errors:
            raise ValidationError(import_validation_errors)
        super().validate_instance(instance, import_validation_errors, validate_unique)

    def save_instance(self, instance: Any, is_create: bool, row: Mapping[str, Any], **kwargs: Any) -> None:
        """Save the form, serve its declared version and add its declared addresses, as the row's named actor.

        ``load_xref(actor=…)`` names that actor: the form's own checks then ask
        it, so choosing the target asks the target's ``permits``. The loader's
        one transaction holds every write, so a refusal anywhere leaves nothing.
        """

        version = self._declared(row, "version", dict, "a mapping of a version's content")
        aliases = self._declared(row, "aliases", list, "a list of slugs")
        if aliases is not None and not all(isinstance(slug, str) for slug in aliases):
            raise ValidationError({"aliases": "Declare aliases as a list of slugs."})
        actor = kwargs.get("actor")
        self.before_save_instance(instance, row, **kwargs)
        with actor_context(actor) if actor is not None else nullcontext():
            self.do_instance_save(instance, is_create)
            # Absent only when the entry lists it under create_only and the form already exists.
            if version is not None:
                instance.serve(version)
            if aliases is not None:
                instance.ensure_aliases(aliases)
        self.after_save_instance(instance, row, **kwargs)

    def _declared(self, row: Mapping[str, Any], column: str, kind: type, shape: str) -> Any:
        """Return one of the adapter's own columns as declared, or ``None`` when the row omits it."""

        if column not in row:
            return None
        try:
            value = self.fields[column].clean(row)
        except ValueError:
            value = None
        if not isinstance(value, kind):
            raise ValidationError({column: f"Declare {column} as {shape}."})
        return value
