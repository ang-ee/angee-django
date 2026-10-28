"""Render a validated composition as ordinary Django class declarations.

The composition owner selects models, donors and parents. This module only names
imports and emits the single donor → source → concrete-parent inheritance order.
Django constructs fields, managers and descriptors when those classes are loaded.
"""

from __future__ import annotations

from collections.abc import Mapping

from django.db import models

from angee.compose.declarations import ModelDeclaration, render_literal
from angee.compose.model_composition import ModelComposition


def render_models(
    composition: ModelComposition,
    label: str,
    *,
    runtime_module: str = "runtime",
    declarations: Mapping[type[models.Model], ModelDeclaration] | None = None,
) -> str:
    """Return the concrete model module for one source app label."""

    imports: set[str] = {"from typing import ClassVar"}
    bodies: list[str] = []
    for source in composition.ordered_models:
        if source not in composition.sources_by_label.get(label, ()):
            continue
        name = source.__name__
        source_alias = f"Abstract{name}"
        imports.add(_class_import(source, source_alias))
        donors = composition.donors(source)
        donor_aliases = tuple(f"{name}Extension{index}" for index in range(1, len(donors) + 1))
        for donor, alias in zip(donors, donor_aliases, strict=True):
            imports.add(_class_import(donor, alias))
        base_names = [*donor_aliases, source_alias]
        parent = composition.parent(source)
        if parent is not None:
            if parent._meta.app_label == label:
                base_names.append(parent.__name__)
            else:
                parent_alias = f"{name}Parent"
                imports.add(
                    f"from {runtime_module}.{parent._meta.app_label}.models import {parent.__name__} as {parent_alias}"
                )
                base_names.append(parent_alias)
        meta_name = f"{source_alias}.Meta"
        meta_lines = [
            "        abstract = False",
            f"        app_label = {label!r}",
        ]
        constraints = [alias for donor, alias in zip(donors, donor_aliases, strict=True) if donor._meta.constraints]
        if constraints:
            meta_lines.append(
                f"        constraints = [*getattr({meta_name}, 'constraints', []), "
                + ", ".join(f"*{alias}._meta.constraints" for alias in constraints)
                + "]"
            )
        declaration = (declarations or {}).get(source, ModelDeclaration())
        meta_lines.extend(
            f"        {option} = {render_literal(value)}" for option, value in sorted(declaration.meta_options.items())
        )
        body_lines = [
            f"    {attribute} = {render_literal(value)}" for attribute, value in sorted(declaration.attributes.items())
        ]
        body_lines.extend(
            [
                "    compose_contributed_field_origins: ClassVar[tuple[tuple[str, str], ...]] = "
                f"{composition.contributed_field_origins(source)!r}",
                "",
            ]
        )
        lines = [
            f"class {name}({', '.join(base_names)}):",
            f'    """Concrete {name} model."""',
            "",
            *body_lines,
            f"    class Meta({meta_name}):",
            *meta_lines,
        ]
        bodies.append("\n".join(lines))
    return "\n".join(
        [
            '"""Concrete Django models emitted by Angee."""',
            "",
            "from __future__ import annotations",
            "",
            *sorted(imports),
            "",
            "\n\n".join(bodies),
            "",
        ]
    )


def _class_import(model: type[models.Model], alias: str) -> str:
    """Render an import of the declared source class itself."""

    return f"from {model.__module__} import {model.__name__} as {alias}"
