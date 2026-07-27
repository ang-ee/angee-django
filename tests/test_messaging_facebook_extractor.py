"""Tests for Facebook's File-subject archive workflow client."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from django.apps import apps

from angee.addons import addon_contract
from angee.messaging_integrate_facebook import extractor as extractor_module
from angee.messaging_integrate_facebook.autoconfig import SETTINGS
from angee.messaging_integrate_facebook.extractor import FacebookTakeoutExtractor


class _ArchiveFile:
    """Minimal storage.File-shaped ZIP wrapper."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def open_stream(self) -> Any:
        """Open the archived bytes."""

        return self.path.open("rb")


def test_recognizes_facebook_marker_and_rejects_near_miss(tmp_path: Path) -> None:
    """Recognition is a hard bool over safe Facebook marker paths."""

    positive = _zip(tmp_path / "positive.zip", facebook=True)
    negative = _zip(tmp_path / "negative.zip", facebook=False)
    extractor = FacebookTakeoutExtractor()

    assert extractor.recognizes(_ArchiveFile(positive)) is True
    assert extractor.recognizes(_ArchiveFile(negative)) is False


def test_execute_stages_and_delegates_to_the_one_facade(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Execution resolves a channel and never passes or invents message kind."""

    archive = _zip(tmp_path / "facebook.zip", facebook=True)
    channel = SimpleNamespace(sqid="int_facebook")
    calls: list[dict[str, Any]] = []
    heartbeats = 0

    def fake_import(root: Path, target: Any, **kwargs: Any) -> dict[str, int]:
        calls.append({"root": root, "channel": target, **kwargs})
        assert (root / "your_facebook_activity").is_dir()
        return {"messages": 1, "posts": 0, "comments": 0, "connections": 0}

    def heartbeat() -> None:
        nonlocal heartbeats
        heartbeats += 1

    monkeypatch.setattr(extractor_module, "confirmed_facebook_channel", lambda _pk: channel)
    monkeypatch.setattr(extractor_module, "import_archive", fake_import)
    result = FacebookTakeoutExtractor().execute(
        _ArchiveFile(archive),
        "int_facebook",
        SimpleNamespace(heartbeat=heartbeat),
    )

    assert calls[0]["channel"] is channel
    assert calls[0]["resume"] is True
    assert "message_kind" not in calls[0]
    assert result["messages"] == 1
    assert heartbeats == 1


def test_addon_registers_backend_and_both_extractors() -> None:
    """Manifest dependencies and autoconfig expose the complete addon shape."""

    contract = addon_contract(apps.get_app_config("messaging_integrate_facebook"))

    assert contract is not None
    assert "angee.messaging_integrate_meta" in contract.depends_on
    assert SETTINGS["ANGEE_CHANNEL_BACKEND_CLASSES.facebook"].endswith(
        ".FacebookChannelBackend"
    )
    assert SETTINGS[
        "ANGEE_WORKFLOW_ARCHIVE_EXTRACTOR_CLASSES.facebook_takeout"
    ].endswith(".FacebookTakeoutExtractor")
    assert SETTINGS[
        "ANGEE_WORKFLOW_ARCHIVE_EXTRACTOR_CLASSES.facebook_takeout_mount"
    ].endswith(".FacebookMountTakeoutExtractor")


def _zip(path: Path, *, facebook: bool) -> Path:
    """Write one positive Facebook export ZIP or an unrelated ZIP."""

    with zipfile.ZipFile(path, "w") as archive:
        if facebook:
            archive.writestr(
                "your_facebook_activity/personal_information/profile_information/"
                "profile_information.json",
                json.dumps({"profile_v2": {}}),
            )
        else:
            archive.writestr("documents/notes.json", "{}")
    return path
