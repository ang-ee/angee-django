"""Angee wire errors for provenance operations."""

import sys
from pathlib import Path

from rebac.errors import MissingActorError

from angee.base.exceptions import DomainError
from angee.provenance.context import ChangesetRequired as ContextRequired


class CreationKeyConflict(DomainError):
    """An idempotency key was already claimed for different content."""

    code = "CREATION_KEY_CONFLICT"

    def __init__(self, *args: object) -> None:
        Exception.__init__(self, *args or (self.code,))


MANAGEMENT_ENTRYPOINT = "angee.management.main"


class ChangesetRequired(ContextRequired, MissingActorError):
    """Preserve native actor catchability and Angee's management-entrypoint remedy."""

    def __init__(self, message: str) -> None:
        if sys.argv and Path(sys.argv[0]).name == "manage.py":
            message += (
                f" This management command runs without a boundary: manage.py must call {MANAGEMENT_ENTRYPOINT}();"
                " re-render it from the project template."
            )
        super().__init__(message)
