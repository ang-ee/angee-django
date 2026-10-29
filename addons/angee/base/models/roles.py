"""Native abstract role-model declarations for addon-owned REBAC namespaces."""

from __future__ import annotations

import re
import sys
from typing import Any, cast

from django.core.exceptions import ImproperlyConfigured

from angee.base.models.base import AngeeModel


def role_anchor(
    resource_type: str,
    *,
    name: str | None = None,
    module: str | None = None,
    doc: str | None = None,
) -> type[AngeeModel]:
    """Return an abstract, table-less REBAC role anchor for ``resource_type``.

    A const-backed role relation (``admin: <ns>/role // rebac:const=admin`` in an
    addon's ``permissions.zed``) needs a model carrying that ``<ns>/role``
    ``rebac_resource_type`` so the ``rebac.E009`` system check resolves the type;
    the anchor is ``managed = False`` (Django owns no table, there are never any
    rows) and ``runtime = True`` (the composer materializes it into the generated
    runtime, exactly like the hand-rolled anchors it replaces). One adopter
    declares its role in one line::

        StorageRole = role_anchor("storage/role")

    ``name`` defaults to a CamelCase of ``resource_type`` (``storage/role`` ->
    ``StorageRole``); pass it when the module symbol differs from that default
    (e.g. ``TagRole = role_anchor("tags/role", name="TagRole")``). ``module``
    defaults to the caller's module (``sys._getframe``) so the composer scans and
    imports the anchor from the adopting addon; the module symbol you bind must
    match ``name`` so the emitted ``from <addon>.models import <name>`` import
    resolves. **Wrapper hazard:** the frame default captures the *direct* caller, so
    a helper that wraps this factory would capture the helper's module, not the
    adopter's, and emit an import that resolves to the wrong symbol. Call
    ``role_anchor`` directly at module level, or pass ``module=__name__`` when
    indirecting it. The composer verifies the captured module actually binds the
    anchor at emission (``angee.compose.rendering``) and fails loudly on a mis-capture
    rather than emitting a broken import.

    The ``.zed`` fragment stays **co-located and static** — each adopter ships its
    own ``definition <ns>/role`` block beside its models; the factory owns only
    the Django anchor model, never a composer ``.zed`` emission.

    Adopters declare their role in one line beside their own models — for
    example, framework ``storage`` (``StorageRole``) and ``tags`` (``TagRole``).
    """

    anchor_name = name or _role_anchor_name(resource_type)
    anchor_module = module or sys._getframe(1).f_globals.get("__name__", __name__)
    meta = type(
        "Meta",
        (),
        {
            "abstract": True,
            "managed": False,
            "rebac_resource_type": resource_type,
        },
    )
    namespace: dict[str, Any] = {
        "__module__": anchor_module,
        "__qualname__": anchor_name,
        "__doc__": doc or f"Table-less REBAC type anchor for the ``{resource_type}`` namespace.",
        "runtime": True,
        "Meta": meta,
    }
    return cast("type[AngeeModel]", type(anchor_name, (AngeeModel,), namespace))


def _role_anchor_name(resource_type: str) -> str:
    """Return the CamelCase anchor class name derived from a role resource type."""

    parts = [part for part in re.split(r"[^0-9A-Za-z]+", resource_type) if part]
    if not parts:
        raise ImproperlyConfigured(f"role_anchor: invalid resource_type {resource_type!r}")
    return "".join(part[:1].upper() + part[1:] for part in parts)
