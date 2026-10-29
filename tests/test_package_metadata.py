"""Native package metadata feeds capability and catalogue readers without addon imports."""

from __future__ import annotations

import sys

import pytest
import tomlkit
from django.core.exceptions import ImproperlyConfigured

from angee.base.addons import addon_facts
from angee.base.testing.packages import write_addon_distribution
from angee.platform import discovery


def distribution(tmp_path, monkeypatch, name, *, requirements=(), editable=False):
    """Extend canonical fixture metadata only with the catalogue facts under test."""

    app = name.replace("-", ".")
    root = tmp_path / name
    package = root.joinpath(*app.split("."))
    package.mkdir(parents=True)
    dist = write_addon_distribution(package, app, capabilities={
        "platform": {"category": "Data"}, "graphql": {"schema": "schema.custom"},
    }, metadata_root=root)
    (package / "__init__.py").write_text("raise AssertionError('disabled addon imported')\n")
    document = tomlkit.parse((package / "pyproject.toml").read_text())
    document["project"]["description"] = "Package data description"
    document["project"]["dependencies"] = list(requirements)
    (package / "pyproject.toml").write_text(tomlkit.dumps(document))
    info, = root.glob("*.dist-info")
    with (info / "METADATA").open("a") as stream:
        stream.write("Summary: Installed description\nKeywords: one,two\n")
        stream.writelines(f"Requires-Dist: {requirement}\n" for requirement in requirements)
    if not editable:
        # Native RECORD is the wheel package-data contract; no source URL fallback.
        (info / "direct_url.json").unlink()
        (info / "RECORD").write_text(f"{package.relative_to(root)}/pyproject.toml,,\n")
    monkeypatch.syspath_prepend(str(root))
    return dist, package


@pytest.mark.parametrize("editable", (False, True))
def test_disabled_catalogue_uses_distribution_paths_without_imports(tmp_path, monkeypatch, editable):
    dist, _ = distribution(tmp_path, monkeypatch, "probe-poison", editable=editable)
    facts = discovery.facts_from_distribution(dist)
    assert facts == {
        "name": "probe.poison", "distribution": "probe-poison", "requirements": (),
        "description": "Installed description",
        "keywords": ("one", "two"), "category": "Data", "depends_on": (),
    }
    assert addon_facts("probe.poison", "angee.graphql") == {"schema": "schema.custom"}
    assert "probe.poison" not in sys.modules


def test_catalogue_projects_native_requirement_markers_and_direct_references(tmp_path, monkeypatch):
    distribution(tmp_path, monkeypatch, "probe-required")
    distribution(tmp_path, monkeypatch, "probe-optional")
    dist, _ = distribution(tmp_path, monkeypatch, "probe-root", requirements=(
        'probe-required @ https://example.invalid/required.whl ; extra != "excluded"',
        'probe-optional>=3 ; extra == "optional"',
        'plain-library>=1',
    ))
    assert discovery.facts_from_distribution(dist)["depends_on"] == ("probe.required",)


def test_requirement_metadata_overrides_package_toml(tmp_path, monkeypatch):
    distribution(tmp_path, monkeypatch, "probe-required")
    dist, package = distribution(tmp_path, monkeypatch, "probe-root", requirements=("probe-required>=3",))
    path = package / "pyproject.toml"
    path.write_text(path.read_text().replace('["probe-required>=3"]', '[]'))
    assert discovery.facts_from_distribution(dist)["depends_on"] == ("probe.required",)


def test_vcs_projection_uses_explicit_distribution_identity_map():
    document = {
        "project": {
            "name": "probe-root", "entry-points": {"angee.addons": {"probe.root": "probe.root"}},
            "dependencies": ['external-package>=1', 'probe-required>=3; extra == ""'],
        },
    }
    facts = discovery.facts_from_toml(document, dependencies={"probe-required": "different.import_name"})
    assert facts["depends_on"] == ("different.import_name",)
    assert discovery.facts_from_toml({"project": {"name": "ordinary"}}) == {}


@pytest.mark.parametrize("identities", [
    (("Example_Foo_Bar", "example.foo_bar"), ("example-foo-bar", "example.foo.bar")),
    (("example-first", "example.same"), ("example-second", "example.same")),
])
def test_native_identity_index_refuses_ambiguous_distribution_or_app_claims(identities):
    with pytest.raises(ImproperlyConfigured, match="Conflicting catalogue identity"):
        discovery.package_identity_index(identities)


def test_identity_index_accepts_the_same_installed_and_catalogue_identity():
    assert discovery.package_identity_index((
        ("Example_Foo_Bar", "example.foo_bar"), ("example-foo-bar", "example.foo_bar"),
    )) == {"example-foo-bar": "example.foo_bar"}


def test_requirement_projection_refuses_canonical_alias_collisions_before_lookup():
    with pytest.raises(ImproperlyConfigured, match="Conflicting catalogue identity"):
        discovery.requirement_addons(("example-foo-bar>=1",), {
            "Example_Foo_Bar": "example.foo_bar", "example-foo-bar": "example.foo.bar",
        })
