"""The markup a message template is written in and the grammar of the values it declares.

:class:`MessageTemplateFormat` and :func:`validate_variables` live here, in a
module that imports neither the model nor the database, so the model and any
other reader of a row's declared values import one owner without a cycle. A
host's first migration records ``validate_variables`` by this module's path (the
format's choices it writes as literals).
"""

from __future__ import annotations

import re
from itertools import islice
from typing import Any

from django.core.exceptions import ValidationError
from django.db import models


class MessageTemplateFormat(models.TextChoices):
    """The markup a message template's ``source`` is written in."""

    MARKDOWN = "markdown", "Markdown"
    HTML = "html", "HTML"


RESERVED_VARIABLE_ROOTS = frozenset({"brand", "lang", "dir", "subject", "preheader", "forloop", "block"})
"""Names no declared value starts with: the mail around a row supplies them (the brand, the language and its
writing direction, the subject and the preheader), and Django's template engine names its loops and blocks so."""

MAX_VARIABLE_SEGMENTS = 8
"""The most names a declared path joins, so following a declared path stays a short walk."""

_VARIABLE_PATH = re.compile(r"[a-z][a-z0-9_]*(?:\.(?:[a-z][a-z0-9_]*|\*))*")


def validate_variables(variables: Any, *, max_paths: int = 50) -> None:
    """Refuse ``variables`` that are not an object of declared paths to their descriptions.

    Each key is a dotted path of at most :data:`MAX_VARIABLE_SEGMENTS` lower-case
    names (``order.total``) whose later segments may be ``*``, every item of a list
    (``tags.*`` is a list of scalars); each value is a string describing it. There
    are at most ``max_paths`` keys: a row's own declaration takes the default, and a
    caller judging several rows' declarations together passes its own limit. No
    path is a proper prefix of another (``order`` and ``order.total`` are not both
    declared), no path reads one value both as a list and as an object
    (``items.*`` beside ``items.pop``), and no path starts with a name in
    :data:`RESERVED_VARIABLE_ROOTS`. The first offending key is named. The check
    takes time linear in the number and length of the keys, since a console insert
    runs it before it checks who may insert.
    """

    if not isinstance(variables, dict):
        raise ValidationError("Declare variables as an object of paths to descriptions.", code="invalid_variables")
    if len(variables) > max_paths:
        extra = next(islice(variables, max_paths, None))
        raise ValidationError(
            "Declare at most %(limit)s variables; %(path)s is one too many.",
            code="too_many_variables", params={"limit": max_paths, "path": extra},
        )
    for path, description in variables.items():
        if isinstance(path, str) and path.count(".") >= MAX_VARIABLE_SEGMENTS:
            raise ValidationError(
                "%(path)s joins more than %(limit)s names.",
                code="variable_too_deep", params={"path": path, "limit": MAX_VARIABLE_SEGMENTS},
            )
        if not isinstance(path, str) or not _VARIABLE_PATH.fullmatch(path):
            raise ValidationError(
                "%(path)s is not a dotted path of lower-case names, each later one a name or *.",
                code="invalid_variable_path", params={"path": path},
            )
        if not isinstance(description, str):
            raise ValidationError(
                "Describe %(path)s in words.", code="invalid_variable_description", params={"path": path},
            )
        if path.split(".", 1)[0] in RESERVED_VARIABLE_ROOTS:
            raise ValidationError(
                "%(path)s starts with a reserved name.", code="reserved_variable", params={"path": path},
            )
    # Each path's parents, at most seven of them, say what the path reads them as:
    # a parent that is itself declared is a proper prefix, and a parent read both
    # through ``*`` and through a name is both a list and an object.
    read_as: dict[str, tuple[bool, str]] = {}
    for path in variables:
        segments = path.split(".")
        for depth in range(1, len(segments)):
            parent = ".".join(segments[:depth])
            if parent in variables:
                raise ValidationError(
                    "%(path)s is declared and so is %(following)s inside it: declare only the inner paths.",
                    code="variable_prefix", params={"path": parent, "following": path},
                )
            as_list = segments[depth] == "*"
            first_as_list, first = read_as.setdefault(parent, (as_list, path))
            if first_as_list != as_list:
                raise ValidationError(
                    "%(path)s and %(other)s read %(parent)s both as a list and as an object.",
                    code="variable_list_and_object", params={"path": first, "other": path, "parent": parent},
                )
