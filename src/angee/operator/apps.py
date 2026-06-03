"""Django config for Angee's operator addon."""

from __future__ import annotations

from angee.base.apps import BaseAddonConfig


class OperatorConfig(BaseAddonConfig):
    """Source app manifest for the operator daemon bridge.

    The addon holds no Django state: it contributes one console GraphQL field
    (``operatorConnection``) and the REBAC schema that gates it. The daemon owns
    all stack/service/source/workspace lifecycle; this addon only hands an
    authorized browser the endpoint and credential to reach it directly.
    """

    default = True
    name = "angee.operator"
    label = "operator"
    depends_on = ("base", "iam")
