"""Only a donor's own concrete columns admit new fragment field gates."""

from pathlib import Path

import pytest
from django.apps import apps
from django.core.management import call_command
from django.db import models
from django.test.utils import isolate_apps
from rebac import RebacMixin
from rebac.models import SchemaDefinition
from rebac.schema import parse_zed
from rebac.schema.parser import validate_schema

from angee.compose.model_composition import ModelComposition
from angee.compose.permissions import (
    SchemaExtensionError,
    apply_schema_paths,
    extension_source_map,
    merged_schema_relpath,
    merged_schemas,
    render_zed,
)
from angee.compose.runtime import Runtime
from tests.conftest import installed_field_owners, make_addon
from tests.test_model_composition import modules as modules
from tests.test_model_composition import source
from tests.test_zed_extensions import _base_addon, _contrib_addon

FIELD_OWNERS = {"demo/thing": {"receipt": "contrib", "recipient": "contrib", "foreign": "other"}}


@pytest.mark.parametrize("verb", ["read", "write"])
def test_owned_gate_can_narrow_using_base_and_contributed_relations(tmp_path, verb):
    base = _base_addon(tmp_path)
    fragment = _contrib_addon(tmp_path, f"""
definition demo/thing {{
    relation reviewer: auth/user
    permission {verb}__receipt = (read + reviewer) & (owner - reviewer)
}}
""")
    schema = merged_schemas([base, fragment], field_owners=FIELD_OWNERS)["base"]
    assert validate_schema(schema) == []
    definition = schema.get_definition("demo/thing")
    gate = next(p for p in definition.permissions if p.name == f"{verb}__receipt")
    expected = parse_zed((Path(fragment.path) / "permissions.extends.zed").read_text())
    assert gate == expected.definitions[0].permissions[0]


@pytest.mark.parametrize("field", ["owner", "foreign", "members", "missing", "recipient_id"])
@pytest.mark.parametrize("verb", ["read", "write"])
def test_gate_on_base_foreign_many_to_many_unknown_or_attname_is_refused(tmp_path, field, verb):
    base = _base_addon(tmp_path)
    fragment = _contrib_addon(tmp_path, f"definition demo/thing {{ permission {verb}__{field} = owner }}")
    with pytest.raises(SchemaExtensionError) as caught:
        merged_schemas([base, fragment], field_owners=FIELD_OWNERS)
    message = str(caught.value)
    assert "contrib" in message and "demo/thing" in message and field in message
    if field == "foreign":
        assert "other" in message
    if field == "recipient_id":
        assert "canonical field name 'recipient'" in message


@pytest.mark.parametrize("owners", [None, {}])
def test_missing_field_ownership_fails_closed(tmp_path, owners):
    base = _base_addon(tmp_path)
    fragment = _contrib_addon(tmp_path, "definition demo/thing { permission read__receipt = owner }")
    with pytest.raises(SchemaExtensionError, match="read__receipt"):
        merged_schemas([base, fragment], field_owners=owners)


def test_new_non_gate_permission_remains_forbidden(tmp_path):
    base = _base_addon(tmp_path)
    fragment = _contrib_addon(tmp_path, "definition demo/thing { permission approve = owner }")
    with pytest.raises(SchemaExtensionError, match="approve"):
        merged_schemas([base, fragment], field_owners=FIELD_OWNERS)


def test_gate_already_declared_by_base_remains_an_additive_arm(tmp_path):
    base = _base_addon(tmp_path, """
definition demo/thing {
    relation owner: auth/user
    permission read__receipt = owner
}
""")
    fragment = _contrib_addon(tmp_path, """
definition demo/thing {
    relation reviewer: auth/user
    permission read__receipt = reviewer
}
""")
    schema = merged_schemas([base, fragment])["base"]
    assert "permission read__receipt = (owner + reviewer)" in render_zed("base", schema)


@pytest.mark.parametrize("reverse", [False, True])
def test_another_fragment_cannot_extend_a_contributed_gate_in_either_order(tmp_path, reverse):
    base = _base_addon(tmp_path)
    owner = _contrib_addon(tmp_path, "definition demo/thing { permission read__receipt = owner }")
    other = _contrib_addon(tmp_path, "definition demo/thing { permission read__receipt = owner }", name="other")
    fragments = [owner, other] if not reverse else [other, owner]
    with pytest.raises(SchemaExtensionError, match="other: demo/thing.*read__receipt"):
        merged_schemas([base, *fragments], field_owners=FIELD_OWNERS)


def test_gate_rendering_is_byte_identical_for_both_input_orders(tmp_path):
    base = _base_addon(tmp_path)
    owner = _contrib_addon(tmp_path, "definition demo/thing { permission read__receipt = owner }")
    other = _contrib_addon(tmp_path, "definition demo/thing { permission write__foreign = owner }", name="other")
    assert extension_source_map([base, owner, other], field_owners=FIELD_OWNERS) == extension_source_map(
        [other, owner, base], field_owners=FIELD_OWNERS,
    )


@pytest.mark.parametrize("prefix", ["", "tenant/"])
@isolate_apps()
def test_composer_maps_only_canonical_donor_columns_and_runtime_emits_the_gate(modules, tmp_path, settings, prefix):
    settings.REBAC_TYPE_PREFIX = prefix
    create, _emit = modules
    base, base_module = create("gate_base")
    donor, donor_module = create("gate_donor")
    for config in (base, donor):
        Path(config.path).mkdir(parents=True, exist_ok=True)
    source(
        base_module, "Root", base.label, bases=(RebacMixin,), runtime=True,
        meta={"rebac_resource_type": "demo/thing"},
    )
    source(base_module, "Plain", base.label, runtime=True)
    source(
        donor_module, "RootExtension", donor.label, extends=f"{base.label}.Root",
        receipt=models.CharField(max_length=40),
        recipient=models.ForeignKey("auth.User", null=True, on_delete=models.SET_NULL),
        members=models.ManyToManyField("auth.User"),
    )
    source(donor_module, "PlainExtension", donor.label, extends=f"{base.label}.Plain", code=models.IntegerField())
    target = f"{prefix}demo/thing"
    (Path(base.path) / "permissions.zed").write_text(f"definition {target} {{ permission read = authenticated }}")
    (Path(donor.path) / "permissions.extends.zed").write_text(
        f"definition {target} {{ permission read__receipt = read }}",
    )
    composition = ModelComposition.discover((donor, base))
    assert composition.field_gate_owners() == {target: {"receipt": donor.name, "recipient": donor.name}}
    runtime = Runtime((base, donor), composition, runtime_dir=tmp_path / "runtime")
    sources = runtime.render_sources()
    assert "permission read__receipt = read" in sources[merged_schema_relpath(base.name)]


@pytest.mark.django_db
def test_emitted_gate_is_stored_after_native_permission_sync(tmp_path):
    configs = list(apps.get_app_configs())
    donor = make_addon(name="tests.gate_fragment", path=tmp_path / "fragment")
    (Path(donor.path) / "permissions.extends.zed").write_text(
        "definition scopedemo/doc { permission read__receipt = read }",
    )
    configs.append(donor)
    owners = installed_field_owners(configs)
    owners.setdefault("scopedemo/doc", {})["receipt"] = donor.name
    sources = extension_source_map(configs, field_owners=owners)
    runtime = tmp_path / "runtime"
    for relative, text in sources.items():
        path = runtime / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    apply_schema_paths(configs, runtime, sources=sources)
    call_command("rebac", "sync", verbosity=0)
    definition = SchemaDefinition.objects.get(resource_type="scopedemo/doc")
    assert definition.permissions.filter(name="read__receipt").exists()
