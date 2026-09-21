"""Content write owners retain selected aliases through reads and legacy hooks."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import router
from rebac import actor_context, system_context

from angee.dashboards import models as dashboard_models
from angee.dashboards.models import Dashboard, DashboardConflictError
from angee.intake import models as intake_models
from angee.knowledge import signals as knowledge_signals
from angee.knowledge.models import MarkdownPageManager
from angee.spaces import models as spaces_models
from tests.conftest import MarkdownPage, Page, create_user, vault_for
from tests.spaces_models import Group
from tests.test_transitions import TransitionRouter


@pytest.mark.parametrize(
    ("method", "args", "expected"),
    [
        ("append", ("tail",), "# Heading\n\noriginal\n\ntail"),
        ("prepend", ("head",), "head\n\n# Heading\n\noriginal"),
        ("replace_unique", ("original", "changed"), "# Heading\n\nchanged"),
        ("patch_section", ("Heading", "replace", "changed"), "# Heading\n\nchanged"),
    ],
)
def test_markdown_edits_read_and_dispatch_on_selected_alias(
    knowledge_tables: None,
    database_alias: Callable[[str], AbstractContextManager[str]],
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    args: tuple[str, ...],
    expected: str,
) -> None:
    """Splice reads avoid the read router; legacy writer overrides keep their shape."""

    owner = create_user(f"content-{method}")
    vault = vault_for(owner)
    with actor_context(owner):
        page = Page.objects.create_in(vault, title="Edit me")
        markdown = MarkdownPage.objects.write_body(page, "# Heading\n\noriginal")
    observed: list[tuple[str | None, str | None, str | None]] = []

    def legacy_write(self: Any, target: Any, body: str, *, expected_hash: str | None = None) -> str:
        observed.append((self._db, target._state.db, expected_hash))
        return body

    monkeypatch.setattr(MarkdownPageManager, "write_body", legacy_write)
    with database_alias("content_writer") as using, system_context(reason="test.content.alias"):
        monkeypatch.setattr(router, "routers", [TransitionRouter("wrong_writer")])
        page._state.db = "wrong_instance"
        edited = getattr(MarkdownPage.objects, method)(
            page, *args, expected_hash=markdown.body_hash, using=using
        )
        assert edited == expected
        assert observed == [(using, using, markdown.body_hash)]


def test_backlink_signal_pins_legacy_rebuilder_to_signal_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit Django save alias wins over stale instance affinity."""

    instance = SimpleNamespace(
        _meta=SimpleNamespace(label_lower="knowledge.markdownpage", app_label="knowledge"),
        _state=SimpleNamespace(db="wrong_instance", adding=False),
    )
    manager = Mock()
    model = SimpleNamespace(_default_manager=manager)
    monkeypatch.setattr(knowledge_signals.apps, "get_model", lambda *args: model)
    knowledge_signals.rebuild_backlinks(object, instance, using="writer")
    manager.db_manager.assert_called_once_with("writer")
    manager.db_manager.return_value.rebuild_for.assert_called_once_with(instance)
    assert instance._state.db == "writer"


def test_channel_capture_preserves_legacy_trigger_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    """The channel and message are pinned before calling an old trigger override."""

    message = SimpleNamespace(_state=SimpleNamespace(db="old_message", adding=False))
    channel = SimpleNamespace(_state=SimpleNamespace(db="old_channel", adding=False))
    queue = object()
    manager = Mock()

    def should_capture(candidate: Any) -> bool:
        assert candidate is message
        assert channel._state.db == message._state.db == "writer"
        return True

    channel.should_capture_message = should_capture
    monkeypatch.setattr(intake_models.apps, "get_model", lambda *args: SimpleNamespace(objects=manager))
    related = Mock(return_value=queue)
    monkeypatch.setattr(intake_models, "related_on", related)
    result = intake_models.ChannelIntake.capture_ingested_message(channel, message, using="writer")
    related.assert_called_once_with(channel, "intake_queue", using="writer")
    manager.db_manager.assert_called_once_with("writer")
    manager.db_manager.return_value.capture_from_message.assert_called_once_with(message, queue=queue)
    assert result is manager.db_manager.return_value.capture_from_message.return_value


def test_group_nondefault_visibility_fails_before_transaction(monkeypatch: pytest.MonkeyPatch) -> None:
    """No group row can commit before unsupported relationship routing is rejected."""

    atomic = Mock(side_effect=AssertionError("The guard must precede all database work."))
    monkeypatch.setattr(spaces_models.transaction, "atomic", atomic)
    with pytest.raises(
        ImproperlyConfigured,
        match="Group visibility relationship writes: the default authorization database is required",
    ):
        Group(name="Routed group").save(using="writer")
    atomic.assert_not_called()


@pytest.mark.parametrize("stale", [False, True])
def test_dashboard_archive_cas_uses_explicit_write_alias(monkeypatch: pytest.MonkeyPatch, stale: bool) -> None:
    """The model owns archive permission, the alias-bound row lock, CAS, and save."""

    locked = Mock(revision=3, is_archived=False)
    locked.sudo.return_value = locked
    rows = Mock()
    rows.get.return_value = locked

    class ArchiveTarget:
        Scope = Dashboard.Scope
        scope = "personal"
        pk = 7
        _state = SimpleNamespace(db="persisted_writer", adding=False)
        system_queryset = Mock(return_value=rows)
        actor = Mock(return_value="actor")
        has_access = Mock(return_value=True)

    atomic = Mock(side_effect=lambda **kwargs: nullcontext())
    monkeypatch.setattr(dashboard_models.transaction, "atomic", atomic)
    target = ArchiveTarget()
    if stale:
        with pytest.raises(DashboardConflictError):
            Dashboard.set_personal_archived(target, archived=True, expected_revision=2, using="default")
        locked.save.assert_not_called()
    else:
        Dashboard.set_personal_archived(target, archived=True, expected_revision=3, using="default")
        locked.save.assert_called_once_with(using="default", update_fields=["is_archived", "revision"])
        assert locked.is_archived is True
        assert locked.revision == 4
    ArchiveTarget.system_queryset.assert_called_once_with(using="default", lock=("self",))
    atomic.assert_called_once_with(using="default")
