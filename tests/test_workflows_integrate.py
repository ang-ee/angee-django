"""Focused archive safety and typed extractor contract checks."""

from __future__ import annotations

import io
import stat
import zipfile
from pathlib import PurePosixPath

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from angee.storage.archives import (
    ArchiveError,
    BoundedReader,
    archive_entries,
    safe_member_name,
    stage_subtree,
)
from angee.workflows_integrate.archive_steps import (
    ArchiveExtractor,
)


class FileExtractor(ArchiveExtractor):
    """Neutral implementation for registry verification."""

    key = "file_test"
    label = "File import"
    target_resource = "storage.Drive"

    def recognizes(self, subject):
        return True

    def execute(self, subject, target_pk, reporter):
        reporter.heartbeat()
        return {"target": target_pk}


def _zip(entries):
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        for name, value in entries:
            archive.writestr(name, value)
    content.seek(0)
    return zipfile.ZipFile(content)


def test_safe_zip_stages_only_selected_subtree():
    with _zip([("left/one.txt", "one"), ("right/two.txt", "two")]) as archive:
        with stage_subtree(archive, PurePosixPath("left")) as root:
            assert (root / "one.txt").read_text() == "one"
            assert not (root.parent.parent / "right" / "two.txt").exists()


def test_safe_zip_rejects_traversal_duplicate_and_links():
    for name in ("../escape", "/absolute", "a\\b"):
        with pytest.raises(ArchiveError):
            safe_member_name(name)
    with pytest.warns(UserWarning, match="Duplicate name"), _zip([("same", "one"), ("same", "two")]) as archive:
        with pytest.raises(ArchiveError, match="repeats"):
            archive_entries(archive)
    link = zipfile.ZipInfo("link")
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with _zip([(link, "outside")]) as archive:
        with pytest.raises(ArchiveError, match="symbolic link"):
            with stage_subtree(archive, PurePosixPath(".")):
                pass


def test_bounded_zip_reader_refuses_unbounded_and_excess_reads():
    bounded = BoundedReader(io.BytesIO(b"abcdef"), limit=3)
    with pytest.raises(ArchiveError):
        bounded.read()
    assert bounded.read(2) == b"ab"
    with pytest.raises(ArchiveError):
        bounded.read(2)


@override_settings(ANGEE_WORKFLOW_ARCHIVE_EXTRACTOR_CLASSES={"file_test": f"{__name__}.FileExtractor"})
def test_registry_uses_canonical_storage_models_and_stable_keys():
    assert ArchiveExtractor.registered_classes() == (FileExtractor,)
    assert ArchiveExtractor.resolve_class("file_test") is FileExtractor
    with pytest.raises(ImproperlyConfigured):
        ArchiveExtractor.resolve_class("missing")


def test_archive_targets_belong_to_gate_configuration():
    from angee.workflows_integrate.archive_steps import ArchiveGateConfig
    config = ArchiveGateConfig(mappings=[{"extractor": "first", "target": "drv_example"}])
    assert config.mappings[0].target == "drv_example"
    assert set(ArchiveGateConfig.model_fields) == {"assignee", "mappings"}
