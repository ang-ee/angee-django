"""Real archive, editable and metadata contracts for the layout backend."""

import ast
import email
import tarfile
import zipfile
from pathlib import Path

import pytest

import hatch_angee


@pytest.fixture
def addon(tmp_path, monkeypatch):
    package = tmp_path / "addons/acme/notes"
    package.mkdir(parents=True)
    (tmp_path / ".gitignore").write_text("node_modules/\n")
    (package / "__init__.py").write_text('VALUE = 42\n')
    (package / "pyproject.toml").write_text('''build-system = { requires = ["hatch-angee>=1,<2"], build-backend = "hatch_angee" }
[project]
name = "acme-notes"
version = "1.0.0"
dependencies = ["django>=6.1"]
entry-points."angee.addons"."acme.notes" = "acme.notes"
[tool.angee]
category = "Data"
''')
    for folder in ("web/src", "templates/notes", "skills/notes", "web/node_modules/unused"):
        target = package / folder / "file.txt"
        target.parent.mkdir(parents=True)
        target.write_text(folder)
    monkeypatch.chdir(package)
    return package


def wheel_files(path):
    with zipfile.ZipFile(path) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def test_wheel_editable_sdist_and_metadata_share_authored_facts(addon, tmp_path, monkeypatch):
    out = tmp_path / "dist"
    out.mkdir()
    expected = {"acme/notes/__init__.py", "acme/notes/pyproject.toml", "acme/notes/web/src/file.txt",
                "acme/notes/templates/notes/file.txt", "acme/notes/skills/notes/file.txt"}
    direct = wheel_files(out / hatch_angee.build_wheel(str(out)))
    assert {name for name in direct if ".dist-info/" not in name} == expected
    metadata = email.message_from_bytes(next(value for name, value in direct.items() if name.endswith("/METADATA")))
    assert metadata.get_all("Requires-Dist") == ["django>=6.1"]
    assert metadata["Requires-Python"] is None
    assert metadata["License-Expression"] is None
    assert next(value for name, value in direct.items() if name.endswith("/entry_points.txt")) == b"[angee.addons]\nacme.notes = acme.notes\n"
    prepared = out / hatch_angee.prepare_metadata_for_build_wheel(str(out)) / "METADATA"
    assert prepared.read_bytes() == next(value for name, value in direct.items() if name.endswith("/METADATA"))
    editable = wheel_files(out / hatch_angee.build_editable(str(out)))
    pth = next(value for name, value in editable.items() if name.endswith(".pth"))
    assert Path(pth.decode().strip()) == addon.parents[1]
    source = out / hatch_angee.build_sdist(str(out))
    extracted = tmp_path / "unpacked"
    with tarfile.open(source) as archive:
        archive.extractall(extracted, filter="data")
    monkeypatch.chdir(next(extracted.iterdir()))
    rebuilt = wheel_files(out / hatch_angee.build_wheel(str(out)))
    assert rebuilt == direct


def test_authored_hatch_configuration_wins(addon, tmp_path):
    with (addon / "pyproject.toml").open("a") as stream:
        stream.write('\n[tool.hatch.build]\nexclude = ["web", "skills", "templates"]\n')
    archive = wheel_files(tmp_path / hatch_angee.build_wheel(str(tmp_path)))
    assert "acme/notes/web/src/file.txt" not in archive


@pytest.mark.parametrize("replacement", [
    ('name = "acme-notes"', 'name = "wrong"'),
    ('entry-points."angee.addons"."acme.notes" = "acme.notes"', ''),
    ('entry-points."angee.addons"."acme.notes" = "acme.notes"',
     'entry-points."angee.addons"."acme.notes" = "acme.notes"\nentry-points."angee.addons"."acme.more" = "acme.more"'),
])
def test_invalid_identity_fails_before_build(addon, tmp_path, replacement):
    target = addon / "pyproject.toml"
    target.write_text(target.read_text().replace(*replacement))
    with pytest.raises(ValueError):
        hatch_angee.build_wheel(str(tmp_path))


def test_layout_mismatch_fails(addon, tmp_path, monkeypatch):
    wrong = addon.with_name("wrong")
    addon.rename(wrong)
    monkeypatch.chdir(wrong)
    with pytest.raises(ValueError, match="package directory"):
        hatch_angee.build_wheel(str(tmp_path))


def test_backend_reads_no_capability_or_dependency_contracts():
    source = Path(hatch_angee.__file__).read_text()
    tree = ast.parse(source)
    imports = {node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert imports <= {"__future__", "pathlib", "typing", "hatchling"}
    assert 'config["tool"]' not in source
    assert 'project["dependencies"]' not in source
    assert not any(path.name in {"manifest.py", "discovery.py", "compile.py", "hooks.py", "graph.py"}
                   for path in Path(hatch_angee.__file__).parent.glob("*.py"))




def test_exact_editable_metadata_preserves_native_runtime_requirement(addon, tmp_path):
    with (addon / "pyproject.toml").open("a") as stream:
        stream.write('\n[tool.hatch.build]\ndev-mode-dirs = []\ndev-mode-exact = true\n')
    prepared = tmp_path / hatch_angee.prepare_metadata_for_build_editable(str(tmp_path)) / "METADATA"
    expected = email.message_from_bytes(prepared.read_bytes()).get_all("Requires-Dist")
    editable = wheel_files(tmp_path / hatch_angee.build_editable(str(tmp_path)))
    actual = email.message_from_bytes(next(value for name, value in editable.items() if name.endswith("/METADATA")))
    assert actual.get_all("Requires-Dist") == expected
    assert "editables~=0.3" in expected
