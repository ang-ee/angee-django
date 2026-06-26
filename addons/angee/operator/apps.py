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
    angee_web_package = "@angee/operator"
    # The daemon owns its GraphQL schema (refresh the committed SDL with
    # `manage.py operator_schema`). It is not a Django Angee schema, so it joins
    # the unified codegen as an external contribution: the composer deposits this
    # SDL into runtime/schemas/operator.graphql and the `angee-web-codegen` CLI
    # generates runtime/gql/operator/ (client preset + a bare `types` module the
    # console re-exports) from the daemon document file.
    angee_web_codegen = {
        "schema": "operator",
        "sdl": "schema/operator.graphql",
        "documents": "documents.daemon.ts",
        "types": True,
    }
    name = "angee.operator"
    label = "operator"
    depends_on = ("angee.iam",)
    schemas = "schema.schemas"
    permissions = "permissions.zed"
