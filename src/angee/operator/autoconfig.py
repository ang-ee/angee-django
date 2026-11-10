"""Settings fragments required by the Angee operator bridge."""

from __future__ import annotations

from typing import TypedDict

DEFAULT_TOKEN_TTL = "1h"

_Settings = TypedDict(
    "_Settings",
    {
        "ANGEE_OPERATOR_TOKEN_TTL": str,
        "ANGEE_OPERATOR_TOKEN_SCOPE": tuple[str, ...],
        "ANGEE_OPERATOR_GRAPHQL_ENDPOINT": str,
        "ANGEE_OPERATOR_URL": str | None,
        "ANGEE_OPERATOR_TOKEN": str | None,
        "ANGEE_OPERATOR_RESTART_JOB": str | None,
    },
)

SETTINGS: _Settings = {
    "ANGEE_OPERATOR_TOKEN_TTL": DEFAULT_TOKEN_TTL,
    "ANGEE_OPERATOR_TOKEN_SCOPE": (),
    "ANGEE_OPERATOR_GRAPHQL_ENDPOINT": "/operator/graphql",
    "ANGEE_OPERATOR_URL": None,
    "ANGEE_OPERATOR_TOKEN": None,
    "ANGEE_OPERATOR_RESTART_JOB": None,
}
"""Django settings contributed when the operator addon is installed."""
