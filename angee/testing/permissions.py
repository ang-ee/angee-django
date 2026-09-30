"""Native permission-schema composition for partial source-test model graphs."""

from collections.abc import Iterable
from pathlib import Path

from django.apps import AppConfig, apps
from rebac.resources import model_resource_type
from rebac.schema import ConstBinding, FieldBinding, parse_zed, render_zed, resolve_schema_path

from angee.compose.permissions import apply_schema_paths, merged_schema_relpath
from angee.fs import write_atomic


def bind_test_permission_schemas(configs: Iterable[AppConfig], runtime: Path) -> None:
    """Bind source-test policy to the concrete models installed by that test host.

    A bare test host imports abstract addon sources without composing every
    model. Omit definitions requiring an absent source model, keeping virtual
    roles and native validation of every backing on a concrete model intact.
    Fully composed hosts keep their complete policy. The restoring fixture owns
    the temporary AppConfig bindings.
    """

    resource_types = {model_resource_type(model) for model in apps.get_models()}
    for config in configs:
        source = resolve_schema_path(config)
        if source is None:
            continue
        schema = parse_zed(source.read_text(encoding="utf-8"))
        definitions = [
            definition for definition in schema.definitions
            if definition.resource_type in resource_types or not any(
                isinstance(relation.backing, FieldBinding)
                or isinstance(relation.backing, ConstBinding) and relation.backing.filters
                for relation in definition.relations
            )
        ]
        if len(definitions) == len(schema.definitions):
            continue
        schema.definitions = definitions
        relative = merged_schema_relpath(config.name)
        content = render_zed(schema)
        write_atomic(runtime / relative, content)
        apply_schema_paths([config], runtime, sources={relative: content})
