"""Who may open django-silk's recorded requests when a project roots ``silk``."""

from __future__ import annotations

from typing import Any

from angee.base.hooks import resolve_hook


def profiling_permitted(user: Any) -> bool:
    """Admit ``user`` to django-silk's views through the ``ANGEE_PROFILING_PERMISSION`` hook.

    The hook names a ``(user) -> bool`` predicate; IAM contributes its platform-admin
    check. Without a hook every user is refused, so recorded requests never open by default.
    """

    permission = resolve_hook("ANGEE_PROFILING_PERMISSION")
    return permission is not None and bool(permission(user))
