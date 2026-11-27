"""Appearance analysis admits authenticated actors, never the anonymous one, through one bounded window."""

from collections.abc import Iterator
from typing import Any

import pytest
import strawberry
from django.core.cache import cache
from rebac import MissingActorError, SubjectRef, actor_context, anonymous_actor

from angee.appearance_integrate import analyser
from angee.appearance_integrate import schema as appearance_schema
from angee.appearance_integrate.schema import AppearanceIntegrateQuery
from angee.base.cache import increment_window
from angee.graphql.testing.documents import execute_schema
from tests.conftest import addon_schema

_FACTS: dict[str, object] = {
    "final_url": "https://example.com", "title": "Example", "site_name": "Example",
    "colors": [], "neutral_tint": "", "fonts": [], "warnings": [],
}


@pytest.fixture
def analysis(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Record admitted work without making outbound requests."""

    partitions: list[str] = []

    def analyse(url: str, *, cache_partition: str) -> dict[str, object]:
        partitions.append(cache_partition)
        return _FACTS

    monkeypatch.setattr(analyser, "analyse_website", analyse)
    cache.clear()
    yield partitions
    cache.clear()


def test_actor_admission_isolated_and_bounded(analysis: list[str]) -> None:
    """An exhausted actor cannot consume another actor's analysis budget."""

    actor = SubjectRef.of("test/actor", "one")
    with actor_context(actor):
        for _ in range(10):
            assert analyser.analyse_for_actor("https://example.com") == _FACTS
        with pytest.raises(analyser.AppearanceAnalysisError) as refused:
            analyser.analyse_for_actor("https://example.com")
    assert refused.value.code == "RATE_LIMITED"
    with actor_context(SubjectRef.of("test/actor", "two")):
        analyser.analyse_for_actor("https://example.com")
    assert len(analysis) == 11
    assert analysis[:10] == [str(actor.object)] * 10
    assert analysis[-1] != analysis[0]


@pytest.mark.parametrize("failure", [ValueError("expired"), OSError("cache unavailable")])
def test_analysis_cache_failure_keeps_graphql_error_contract(
    analysis: list[str], monkeypatch: pytest.MonkeyPatch, failure: Exception,
) -> None:
    """Admission infrastructure failures never dispatch outbound work."""

    def fail(*args: Any, **kwargs: Any) -> int:
        raise failure

    monkeypatch.setattr(analyser, "increment_window", fail)
    schema = strawberry.Schema(query=AppearanceIntegrateQuery)
    with actor_context(SubjectRef.of("test/actor", "one")):
        result = schema.execute_sync('{ analyseAppearance(url: "https://example.com") { title } }')
    assert result.errors is not None
    assert result.errors[0].extensions == {"code": "SERVICE_UNAVAILABLE"}
    assert analysis == []


def test_analysis_requires_actor_before_outbound_work(analysis: list[str]) -> None:
    """The same admission rule protects direct service and GraphQL callers."""

    with pytest.raises(MissingActorError):
        analyser.analyse_for_actor("https://example.com")
    assert analysis == []


def test_anonymous_caller_is_refused_before_the_rate_window_and_outbound_work(
    analysis: list[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unauthenticated caller holds REBAC's shared anonymous actor, which is refused like no actor.

    The served console schema answers the refusal as every missing actor is answered.
    """

    windows: list[str] = []

    def spend(window_cache: Any, key: str, *, timeout: int) -> int:
        windows.append(key)
        return increment_window(window_cache, key, timeout=timeout)

    monkeypatch.setattr(analyser, "increment_window", spend)
    schema = strawberry.Schema(query=AppearanceIntegrateQuery)
    document = '{ analyseAppearance(url: "https://example.com") { title } }'
    with actor_context(anonymous_actor()), pytest.raises(MissingActorError):
        analyser.analyse_for_actor("https://example.com")
    served = execute_schema(
        addon_schema(appearance_schema.schemas, "console"),
        '{ analyse_appearance(url: "https://example.com") { title } }',
    )

    assert served.errors is not None
    assert [(error.message, error.extensions) for error in served.errors] == [
        ("Authentication required.", {"code": "UNAUTHENTICATED"}),
    ]
    assert (windows, analysis) == ([], [])

    actor = SubjectRef.of("test/actor", "one")
    with actor_context(actor):
        admitted = schema.execute_sync(document)
    assert admitted.errors is None
    assert admitted.data == {"analyseAppearance": {"title": "Example"}}
    assert windows == [f"appearance:analysis-rate:{actor.object}"]
    assert analysis == [str(actor.object)]
