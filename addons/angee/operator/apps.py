"""Django config for Angee's operator addon."""

from __future__ import annotations

from django.apps import AppConfig


class OperatorConfig(AppConfig):
    """Source app manifest for the operator daemon bridge.

    The addon holds no Django models. It contributes one console GraphQL field
    (``operatorConnection``, which mints a scoped browser token) and the REBAC
    schema that gates it, plus the ``OperatorDaemon`` server-side bridge Django
    uses to drive the daemon over its REST API — e.g. provisioning an agent on a
    user's behalf. The daemon still owns all stack/service/source/workspace
    lifecycle; this addon only reaches it (from the browser, or from Django).
    """

    default = True
    angee_addon = True
    default_auto_field = "django.db.models.BigAutoField"
    name = "angee.operator"
    label = "operator"
    depends_on = ("angee.iam",)
    schemas = "schema.schemas"
    permissions = "permissions.zed"
