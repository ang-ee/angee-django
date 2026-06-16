"""Compose-time GraphQL type extension.

The GraphQL parallel to a model ``extends``: a downstream addon contributes
fields onto a type an upstream addon defines, without the upstream addon
referencing anything downstream. Because the schema is built *after* the runtime
is composed, a field projecting a relation the downstream addon owns (e.g. an
``oidc`` refinement of an ``OAuthClient``) resolves its related type from
strawberry-django's model registry — so the extension needs no upward import.

Mark the contributing type with :func:`extends_type` and list it in a schema
bucket's ``type_extensions``; the composer appends its fields to the target after
all addons compose.
"""

from __future__ import annotations

from typing import Any, TypeVar

_T = TypeVar("_T")

_EXTENDS_ATTR = "__angee_extends_type__"


def extends_type(target: Any) -> Any:
    """Mark a Strawberry type as contributing its fields onto ``target`` at build.

    ``target`` is the upstream Strawberry type object (imported downward, which is
    always allowed). The marked type is otherwise an ordinary
    ``@strawberry_django.type(Model)`` bound to the *same model* as ``target``; only
    its fields are merged in — it is never emitted as a standalone GraphQL type.
    """

    def decorate(extension: _T) -> _T:
        setattr(extension, _EXTENDS_ATTR, target)
        return extension

    return decorate


def extension_target(extension: object) -> Any:
    """Return the type one ``type_extensions`` member targets, or ``None``."""

    return getattr(extension, _EXTENDS_ATTR, None)
