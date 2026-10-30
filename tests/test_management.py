"""Angee command attribution composes the native parsed-command context seam."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from types import MappingProxyType

import pytest

from angee.provenance.contrib.angee import context as command_context


@pytest.mark.parametrize("command", sorted(command_context.SERVER_COMMANDS))
def test_long_running_commands_keep_their_own_contexts(monkeypatch: pytest.MonkeyPatch, command: str) -> None:
    """Servers, workers and tests attribute individual units of work."""

    def forbidden(**kwargs):
        raise AssertionError("a long-running process opened a process-wide changeset")

    monkeypatch.setattr(command_context, "changeset", forbidden)
    with command_context.management_context(command, ("manage.py", command), MappingProxyType({})):
        pass


@pytest.mark.parametrize("fail", [False, True])
def test_management_attribution_uses_native_selected_options_and_unwinds(
    monkeypatch: pytest.MonkeyPatch, fail: bool,
) -> None:
    """The Angee adapter attributes the selected database and preserves failure."""

    events = []

    @contextmanager
    def changeset(**options) -> Iterator[None]:
        events.append(options)
        try:
            yield
        finally:
            events.append("closed")

    monkeypatch.setattr(command_context, "changeset", changeset)
    with pytest.raises(RuntimeError, match="command failed") if fail else nullcontext():
        with command_context.management_context(
            "migrate", ("manage.py", "migrate", "--database=selected"), MappingProxyType({"database": "selected"}),
        ):
            if fail:
                raise RuntimeError("command failed")
    assert events == [{
        "fresh": True, "activity": "system", "change_reason": "manage.migrate", "process": True, "using": "selected",
    }, "closed"]
