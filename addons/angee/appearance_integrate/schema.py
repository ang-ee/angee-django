"""Authenticated GraphQL surface for bounded website appearance analysis."""

from __future__ import annotations

import strawberry
from django.core.cache import cache
from django.core.exceptions import ValidationError
from graphql import GraphQLError
from rebac import current_actor

from angee.appearance_integrate.analyser import analyse_website


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

        actor = current_actor()
        if actor is None:
            raise GraphQLError("Authentication required.", extensions={"code": "UNAUTHENTICATED"})
        rate_key = f"appearance:analysis-rate:{actor.object}"
        if cache.add(rate_key, 1, timeout=60):
            count = 1
        else:
            try:
                count = cache.incr(rate_key)
            except ValueError:
                raise GraphQLError("Appearance analysis is temporarily unavailable.", extensions={"code": "SERVICE_UNAVAILABLE"})
        if count > 10:
            raise GraphQLError("Appearance analysis rate limit exceeded.", extensions={"code": "RATE_LIMITED"})
        try:
            facts = analyse_website(url, cache_partition=str(actor.object))
        except (OSError, ValidationError, ValueError) as error:
            raise GraphQLError(str(error), extensions={"code": "BAD_USER_INPUT"}) from error
        return AppearanceAnalysis(**facts)  # type: ignore[arg-type]


schemas = {"console": {"query": [AppearanceIntegrateQuery], "types": [AppearanceAnalysis]}}
