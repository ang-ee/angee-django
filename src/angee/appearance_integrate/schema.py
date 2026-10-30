"""Authenticated GraphQL surface for bounded website appearance analysis."""

from __future__ import annotations

import strawberry
from django.core.exceptions import ValidationError
from graphql import GraphQLError

from angee.appearance_integrate.analyser import AppearanceAnalysisError, analyse_for_actor
from angee.integrate.errors import RequestNotSentError


@strawberry.type
class AppearanceAnalysis:
    final_url: str
    title: str
    site_name: str
    colors: list[str]
    neutral_tint: str
    fonts: list[str]
    warnings: list[str]


@strawberry.type
class AppearanceIntegrateQuery:
    @strawberry.field
    def analyse_appearance(self, url: str) -> AppearanceAnalysis:
        """Analyse public HTML after authentication and a per-actor rate gate."""

        try:
            facts = analyse_for_actor(url)
        except AppearanceAnalysisError as error:
            raise GraphQLError(str(error), extensions={"code": error.code}) from error
        except (RequestNotSentError, OSError, ValidationError, ValueError) as error:
            raise GraphQLError(str(error), extensions={"code": "BAD_USER_INPUT"}) from error
        return AppearanceAnalysis(**facts)  # type: ignore[arg-type]


schemas = {"console": {"query": [AppearanceIntegrateQuery], "types": [AppearanceAnalysis]}}
