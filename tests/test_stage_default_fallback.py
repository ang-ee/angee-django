"""The fallback extension receives ordered, scoped stages after explicit defaults."""

import pytest

from tests.core_seam_models import FallbackContainer, FallbackStage

pytestmark = pytest.mark.django_db


@pytest.fixture
def pipeline():
    container = FallbackContainer.objects.create()
    other = FallbackContainer.objects.create()
    # Conflicting name and insertion ordering catch accidental Meta ordering.
    late = FallbackStage.objects.create(container=container, name="A late", position=9)
    first = FallbackStage.objects.create(container=container, name="Z first", position=1)
    tied = FallbackStage.objects.create(container=container, name="A tied", position=1)
    foreign = FallbackStage.objects.create(container=other, name="Foreign", position=0)
    return container, (first, tied, late), foreign


def test_base_fallback_selects_first_by_position_then_primary_key(pipeline, django_assert_num_queries):
    container, (first, _tied, _late), _foreign = pipeline
    with django_assert_num_queries(1):
        assert FallbackStage.resolve_default(container) == first


def test_explicit_default_wins_without_asking_the_fallback(pipeline, monkeypatch, django_assert_num_queries):
    container, (_first, _tied, late), _foreign = pipeline
    container.default_stage = late
    container.save()

    def forbidden(cls, stages):
        pytest.fail("A valid explicit default must bypass fallback selection.")

    monkeypatch.setattr(FallbackStage, "resolve_default_fallback", classmethod(forbidden))
    with django_assert_num_queries(1):
        assert FallbackStage.resolve_default(container) == late


@pytest.mark.parametrize("default", ["absent", "foreign", "deleted"])
def test_fallback_override_receives_only_the_containers_stages_in_pipeline_order(pipeline, monkeypatch, default):
    container, stages, foreign = pipeline
    if default == "foreign":
        container.default_stage = foreign
    elif default == "deleted":
        container.default_stage_id = foreign.pk
        foreign.delete()
    calls = []

    def choose(cls, queryset):
        calls.append((cls, list(queryset)))
        return queryset.filter(name="A late").first()

    monkeypatch.setattr(FallbackStage, "resolve_default_fallback", classmethod(choose))
    assert FallbackStage.resolve_default(container) == stages[-1]
    assert calls == [(FallbackStage, list(stages))]


def test_empty_container_returns_no_default_even_when_other_containers_have_stages(pipeline):
    container = FallbackContainer.objects.create()
    assert FallbackStage.resolve_default(container) is None


def test_fallback_override_may_deliberately_return_no_stage(pipeline, monkeypatch):
    container, _stages, _foreign = pipeline
    monkeypatch.setattr(FallbackStage, "resolve_default_fallback", classmethod(lambda cls, stages: None))
    assert FallbackStage.resolve_default(container) is None
