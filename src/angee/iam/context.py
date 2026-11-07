"""IAM authorization for parsed native management commands after Django setup."""

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

from rebac import system_context


@contextmanager
def command_context(command: str, argv: Sequence[str], options: Mapping[str, Any]) -> Iterator[None]:
    """Run stock auth commands' permission bookkeeping as audited system work."""

    del argv, options
    if command not in {"createsuperuser", "changepassword"}:
        yield
        return
    with system_context(reason=f"auth.{command}"):
        yield
