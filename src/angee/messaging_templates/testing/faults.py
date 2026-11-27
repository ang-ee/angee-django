"""Faults a test makes a message template render meet: the one supported way to reach ``render_failed``.

Test support only, for Python tests: no story imports it, and it imports no test runner. Each context manager
restores what it replaced on exit.
"""

from __future__ import annotations

import functools
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from django.template.base import Template
from django.template.context import Context
from django.utils.safestring import SafeString

from angee.messaging_templates.engine import register


@contextmanager
def failing_filter(name: str, error: BaseException) -> Iterator[None]:
    """While inside, the message template filter ``name`` raises ``error`` whatever it reads (an engine failure
    whose words the test chooses, a value quoted in them among them); the filter is restored on exit."""

    original = register.filters[name]

    @functools.wraps(original)
    def failing(*args: Any, **kwargs: Any) -> Any:
        raise error

    register.filters[name] = failing
    try:
        yield
    finally:
        register.filters[name] = original


@contextmanager
def failing_render(error: BaseException) -> Iterator[None]:
    """While inside, every Django template render raises ``error``, as an engine failing mid-render would; the render
    is restored on exit."""

    original = Template.render

    def failing(self: Template, context: Context) -> SafeString:
        raise error

    Template.render = failing  # type: ignore[method-assign]
    try:
        yield
    finally:
        Template.render = original  # type: ignore[method-assign]
