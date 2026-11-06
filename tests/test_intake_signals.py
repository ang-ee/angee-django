"""Intake's receiver on messaging's message-ingested seam."""

from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ValidationError

from angee.base.exceptions import RecordAccessSubjectRefused
from angee.intake import signals


class _Message:
    pk = 455262
    channel_id = 10

    def __init__(self, transport: Any) -> None:
        self.transport = transport

    @property
    def channel(self) -> Any:
        raise AssertionError("the Integration parent row has no intake contribution")

    def transport_channel(self, *, reason: str) -> Any:
        assert reason
        if isinstance(self.transport, Exception):
            raise self.transport
        return self.transport


def test_capture_runs_on_the_messages_transport_channel() -> None:
    captured: list[Any] = []
    message = _Message(type("Channel", (), {"capture_ingested_message": staticmethod(captured.append)})())

    signals.capture_channel_message(sender=None, instance=message)

    assert captured == [message]


@pytest.mark.parametrize("error", [ValidationError("No triage stage"), RecordAccessSubjectRefused()])
def test_capture_refusal_retains_primary_ingest(
    caplog: pytest.LogCaptureFixture,
    error: Exception,
) -> None:
    signals.capture_channel_message(sender=None, instance=_Message(error))

    assert "Skipped intake capture" in caplog.text


@pytest.mark.parametrize(
    "error", [AttributeError("Missing accessor"), TypeError("Wrong contract"), LookupError("Missing required model")],
)
def test_programming_errors_propagate(error: Exception) -> None:
    with pytest.raises(type(error), match=str(error)):
        signals.capture_channel_message(sender=None, instance=_Message(error))


def test_capture_is_inert_without_a_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(signals.apps, "get_model", lambda *_: pytest.fail("no model lookup without a channel"))

    signals.capture_channel_message(sender=None, instance=type("Message", (), {"pk": 1, "channel_id": None})())
