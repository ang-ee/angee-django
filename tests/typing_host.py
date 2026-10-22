"""Static declarations for native history models in the isolated typing host.

HistoryDescriptor owns the runtime classes. Django's migration state and
serializer describe their fields; the normal composer still owns every ordinary
model declaration. The extra classes and their imports never run in Django.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from django.apps import AppConfig


class TypingHostConfig(AppConfig):
    """Project native generated classes after the composed models are populated."""

    name = "tests.typing_host"
    label = "typing_host"

    def ready(self) -> None:
        project_history_models()


def project_history_models() -> bool:
    """Reconcile only composed model modules, keeping runtime classes unchanged."""

    # AppConfig is imported during Django phase 1; model owners are available
    # only here, after phase 2 has constructed the native history descriptors.
    from django.apps import apps
    from django.db import models
    from django.db.migrations.state import ModelState
    from django.db.migrations.writer import MigrationWriter
    from simple_history.manager import HistoryDescriptor

    from angee.compose.rendering import render_models
    from angee.compose.runtime import Runtime
    from angee.fs import GENERATED_SENTINEL, GeneratedTree
    from angee.models.history import ModelHistoryManager
    from angee.models.statements import StatementManager, StatementPairManager

    runtime = Runtime.from_django()
    generated: dict[str, dict[str, type[models.Model]]] = defaultdict(dict)
    for owner in apps.get_models():
        for descriptor in vars(owner).values():
            if not isinstance(descriptor, HistoryDescriptor) or not issubclass(
                descriptor.manager_class, (ModelHistoryManager, StatementManager, StatementPairManager),
            ):
                continue
            model = descriptor.model
            label = model._meta.app_label
            if label in runtime.labels and model.__module__ == f"{runtime.runtime_module}.{label}.models":
                generated[label][model.__name__] = model

    sources: dict[Path, str] = {}
    for label, classes in sorted(generated.items()):
        imports: set[str] = set()
        bodies: list[str] = []
        for name, model in sorted(classes.items()):
            bases: list[str] = []
            inherited_fields: dict[str, models.Field] = {}
            for base in model.__bases__:
                imports.add(f"import {base.__module__}")
                bases.append(f"{base.__module__}.{base.__qualname__}")
                if issubclass(base, models.Model):
                    for field in base._meta.fields:
                        inherited_fields.setdefault(field.name, field)
            lines = [f"class {name}({', '.join(bases)}):"]
            for field_name, field in sorted(ModelState.from_model(model).fields.items()):
                inherited = inherited_fields.get(field_name)
                if inherited is not None and inherited.deconstruct()[1:] == field.deconstruct()[1:]:
                    # Native abstract inheritance already owns this exact field,
                    # including enum identity lost by migration serialization.
                    continue
                expression, dependencies = MigrationWriter.serialize(field)
                imports.update(dependencies)
                lines.append(f"    {field_name} = {expression}")
            lines.extend(["    class Meta:", f"        app_label = {label!r}"])
            bodies.append("\n".join(lines))
        projection = "\n".join([
            "from typing import TYPE_CHECKING",
            "",
            "if TYPE_CHECKING:",
            *(f"    {line}" for line in sorted(imports)),
            "",
            *("\n".join(f"    {line}" for line in body.splitlines()) + "\n" for body in bodies),
        ])
        sources[Path(label) / "models.py"] = (
            render_models(runtime.composition, label, runtime_module=runtime.runtime_module) + "\n" + projection
        )
    return GeneratedTree(
        runtime.runtime_dir,
        sources,
        owns=lambda path: path in sources,
        sentinel=(Path("__init__.py"), GENERATED_SENTINEL),
        clean_root=runtime.runtime_dir,
    ).reconcile(prune=False)
