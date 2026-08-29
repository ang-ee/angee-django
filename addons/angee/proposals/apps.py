"""Django configuration for the proposals addon."""

from __future__ import annotations

from django.apps import AppConfig


class ProposalsConfig(AppConfig):
    """Own PM-backed solicitation, comparison, disclosure, and decision."""

    default = True
    name = "angee.proposals"
