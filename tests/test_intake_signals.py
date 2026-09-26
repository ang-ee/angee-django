"""Intake's receiver on messaging's message-ingested seam."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from angee.intake import signals


class _Rows:
    def __init__(self, row: Any) -> None:
        self.row = row
        self.calls: list[tuple[str, Any]] = []

    def select_related(self, *fields: str) -> _Rows:
        self.calls.append(("select_related", fields))
        return self

    def filter(self, **lookup: Any) -> _Rows:
        self.calls.append(("filter", lookup))
        return self

    def first(self) -> Any:
        return self.row


def test_capture_resolves_the_concrete_channel_not_the_integration_parent(monkeypatch: pytest.MonkeyPatch) -> None:
    """``Message.channel`` is the Integration parent; intake behavior lives on Channel."""

    captured: list[Any] = []
    channel = SimpleNamespace(pk=10, capture_ingested_message=captured.append)
    rows = _Rows(channel)
    channel_model = SimpleNamespace(_base_manager=rows)
    models = {("messaging", "Channel"): channel_model, ("intake", "Need"): object()}
    monkeypatch.setattr(signals.apps, "get_model", lambda app_label, name: models[app_label, name])

    class _Message:
        pk = 455262
        channel_id = 10

        @property
        def channel(self) -> Any:
            raise AssertionError("the Integration parent row has no intake contribution")

    message = _Message()

    signals.capture_channel_message(sender=None, instance=message)

    assert captured == [message]
    assert ("filter", {"pk": 10}) in rows.calls
    assert ("select_related", ("intake_queue",)) in rows.calls


def test_capture_is_inert_without_a_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        signals.apps, "get_model", lambda *_: pytest.fail("no model lookup without a channel")
    )

    signals.capture_channel_message(sender=None, instance=SimpleNamespace(pk=1, channel_id=None))
