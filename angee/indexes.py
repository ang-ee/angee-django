"""Index primitives shared by Angee source models."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from django.db import models


class PatternOpsIndex(models.Index):
    """A btree index carrying a text-pattern operator class, auto-named per model.

    PostgreSQL will not use a default btree index to serve a prefix
    ``LIKE 'x%'`` under a non-C collation; a ``varchar_pattern_ops`` /
    ``text_pattern_ops`` operator-class index is required, and it rides into the
    migration through :meth:`~django.db.models.Index.deconstruct`. Django forbids
    ``opclasses`` on an :class:`~django.db.models.Index` without an explicit
    ``name``, but an abstract mixin cannot name one index that several concrete
    models each build (the name would collide). This withholds ``opclasses``
    from ``Index.__init__`` so Django auto-names one index per concrete table
    (``set_name_with_model``), then restores the operator class. Backends without
    operator classes (SQLite) drop it and index the column plainly, so the prefix
    query stays correct on every backend — only the plan differs.
    """

    def __init__(self, *args: Any, opclasses: Sequence[str] = (), **kwargs: Any) -> None:
        """Build the index withholding ``opclasses`` from the name-required guard."""

        super().__init__(*args, **kwargs)
        self.opclasses = tuple(opclasses)
