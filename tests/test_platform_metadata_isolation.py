"""Bounded data-only wheel readers and explicit local artifact admission."""

from __future__ import annotations

import io
import zipfile
import zlib

import pytest
from django.core.exceptions import ImproperlyConfigured
from rebac import system_context

from angee.platform_integrate_vcs import wheels
from tests.conftest import Addon, Repository, Source, VcsBridge, make_integration
from tests.wheel_fixtures import wheel_files, write_wheel


def archive_bytes(files, *, compression=zipfile.ZIP_DEFLATED):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=compression) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    output.seek(0)
    return output


@pytest.mark.parametrize("name", ["../escape/pyproject.toml", "/escape/pyproject.toml", "addon\\pyproject.toml"])
def test_wheel_metadata_rejects_escaping_or_aliased_paths(name):
    with pytest.raises(ValueError, match="normalized relative"):
        wheels.wheel_metadata(archive_bytes({**wheel_files(), name: "untrusted"}))


def test_record_cannot_escape_its_metadata_snapshot():
    files = wheel_files()
    record = next(name for name in files if name.endswith("/RECORD"))
    files[record] += "../../outside/pyproject.toml,,\n"
    with pytest.raises(ValueError, match="normalized relative"):
        wheels.wheel_metadata(archive_bytes(files))


def test_code_files_are_never_returned_as_metadata():
    result = wheels.wheel_metadata(archive_bytes(wheel_files(code=True)))
    assert not any(name.endswith(".py") for name in result)
    assert "__init__.py" not in next(value for name, value in result.items() if name.endswith("/RECORD"))


def test_wheel_archive_and_metadata_sizes_are_bounded(monkeypatch):
    stream = archive_bytes(wheel_files())
    monkeypatch.setattr(wheels, "MAX_WHEEL_BYTES", 1)
    with pytest.raises(ValueError, match="archive byte limit"):
        wheels.wheel_metadata(stream)
    monkeypatch.setattr(wheels, "MAX_WHEEL_BYTES", 64 * 1024 * 1024)
    files = wheel_files()
    name = next(name for name in files if name.endswith("/METADATA"))
    files[name] = "x" * (wheels.MAX_METADATA_BYTES + 1)
    with pytest.raises(ValueError, match="file exceeds"):
        wheels.wheel_metadata(archive_bytes(files))


def test_metadata_rejects_non_wheel_compression_methods():
    with pytest.raises(ValueError, match="compression method"):
        wheels.wheel_metadata(archive_bytes(wheel_files(), compression=zipfile.ZIP_BZIP2))


def test_forged_zip_size_cannot_trigger_unbounded_decompression(monkeypatch):
    files = wheel_files()
    metadata_name = next(name for name in files if name.endswith("/METADATA"))
    payload = "x" * 262144
    files[metadata_name] = payload
    original_inventory = zipfile.ZipFile.infolist
    original_decompressor = zipfile._get_decompressor
    lengths = []
    def inventory(archive):
        members = original_inventory(archive)
        for member in members:
            if member.filename == metadata_name:
                member.file_size = 64
                member.CRC = zlib.crc32(payload[:64].encode())
        return members
    class Observe:
        def __init__(self, native):
            self.native = native
        def decompress(self, data, max_length=0):
            lengths.append(max_length)
            return self.native.decompress(data, max_length)
        def __getattr__(self, name):
            return getattr(self.native, name)
    monkeypatch.setattr(zipfile.ZipFile, "infolist", inventory)
    monkeypatch.setattr(zipfile, "_get_decompressor", lambda *args: Observe(original_decompressor(*args)))
    assert wheels.wheel_metadata(archive_bytes(files))[metadata_name] == payload[:64]
    assert lengths and 0 < max(lengths) <= 1024 * 1024 + 1


def local_source(root):
    bridge = make_integration(
        "local-wheel-catalogue", model=VcsBridge, backend_class="local",
        config={"local_root": str(root), "local_name": "local-wheels"},
    )
    bridge.discover_repositories()
    with system_context(reason="tests.catalogue.local_source"):
        return Source.objects.create(repository=Repository.objects.get(name="local-wheels"), kind="addon", path="dist")


def test_local_catalogue_reads_built_wheels_without_importing_source(composed_tables, tmp_path, monkeypatch):
    write_wheel(tmp_path / "dist/local.whl", "local.addon", "local-addon", description="Built locally", code=True)
    (tmp_path / "pyproject.toml").write_text('[build-system]\nrequires=[]\nbuild-backend="must_not_import"\n')
    source = local_source(tmp_path)
    assert source.refresh() == 1
    with system_context(reason="tests.catalogue.local_read"):
        row = Addon.objects.get(name="local.addon")
        assert row.description == "Built locally" and row.vcs_path == "dist/local.whl"


def test_local_source_without_wheels_requires_an_explicit_author_build(composed_tables, tmp_path):
    (tmp_path / "dist").mkdir()
    source = local_source(tmp_path)
    with pytest.raises(ImproperlyConfigured, match="already built wheels"):
        source.refresh()
    with system_context(reason="tests.catalogue.no_implicit_build"):
        source.refresh_from_db()
        assert source.last_synced_at is None
