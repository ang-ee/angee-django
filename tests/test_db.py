"""Write-alias precedence uses Django bindings without consulting read routing."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from django.db import DEFAULT_DB_ALIAS, models, router
from django.test.utils import isolate_apps

from angee.base.db import get_write_alias


@pytest.fixture
def write_model() -> Iterator[type[models.Model]]:
    """Provide a native model without registering it in the shared app registry."""

    with isolate_apps():

        class WriteRecord(models.Model):
            """Table-free model for routing decisions."""

            class Meta:
                """Keep the model local to this test fixture."""

                app_label = "tests"

        yield WriteRecord


class WriteRouter:
    """Record write hints and reject any accidental read-router consultation."""

    def __init__(self) -> None:
        self.calls: list[tuple[type[models.Model], dict[str, object]]] = []

    def db_for_read(self, model: type[models.Model], **hints: object) -> str:
        raise AssertionError("Write alias selection must not consult the read router.")

    def db_for_write(self, model: type[models.Model], **hints: object) -> str:
        self.calls.append((model, hints))
        return "writer"


@pytest.fixture
def write_router(monkeypatch: pytest.MonkeyPatch) -> WriteRouter:
    """Install the double through Django's native router collection."""

    write_router = WriteRouter()
    monkeypatch.setattr(router, "routers", [write_router])
    return write_router


@pytest.fixture(params=["manager", "queryset"])
def bound_owner(request: pytest.FixtureRequest, write_model: type[models.Model]) -> models.Manager | models.QuerySet:
    """Bind each native row-set owner to the same explicit alias."""

    if request.param == "manager":
        return write_model._default_manager.db_manager("bound")
    return write_model._default_manager.using("bound")


def test_explicit_alias_overrides_bound_and_persisted_aliases(
    write_model: type[models.Model],
    write_router: WriteRouter,
    bound_owner: models.Manager | models.QuerySet,
) -> None:
    """A caller's explicit alias is authoritative over every fallback."""

    instance = write_model(pk=1)
    instance._state.adding = False
    instance._state.db = "persisted"

    assert get_write_alias(write_model, using="explicit", bound=bound_owner, instance=instance) == "explicit"
    assert write_router.calls == []


def test_bound_alias_overrides_persisted_alias(
    write_model: type[models.Model],
    write_router: WriteRouter,
    bound_owner: models.Manager | models.QuerySet,
) -> None:
    """Both native binding forms take precedence over instance affinity."""

    instance = write_model(pk=1)
    instance._state.adding = False
    instance._state.db = "persisted"

    assert get_write_alias(write_model, bound=bound_owner, instance=instance) == "bound"
    assert write_router.calls == []


@pytest.fixture(params=["none", "manager", "queryset"])
def unbound_owner(
    request: pytest.FixtureRequest, write_model: type[models.Model]
) -> models.Manager | models.QuerySet | None:
    """Offer no binding or native owners that have no explicit binding."""

    if request.param == "manager":
        return write_model._default_manager
    if request.param == "queryset":
        return write_model._default_manager.all()
    return None


def test_persisted_instance_precedes_write_routing(
    write_model: type[models.Model],
    write_router: WriteRouter,
    unbound_owner: models.Manager | models.QuerySet | None,
) -> None:
    """Unbound row-set owners never replace the persisted instance's alias."""

    instance = write_model(pk=1)
    instance._state.adding = False
    instance._state.db = "persisted"

    assert get_write_alias(write_model, bound=unbound_owner, instance=instance) == "persisted"
    assert write_router.calls == []


@pytest.mark.parametrize("instance_kind", ["none", "new", "new_with_affinity", "persisted_without_alias"])
def test_fallback_uses_write_router_and_preserves_instance_hint(
    write_model: type[models.Model],
    write_router: WriteRouter,
    unbound_owner: models.Manager | models.QuerySet | None,
    instance_kind: str,
) -> None:
    """New-object affinity and a preassigned primary key are not persistence."""

    instance = None if instance_kind == "none" else write_model(pk=1)
    if instance is not None:
        instance._state.adding = instance_kind != "persisted_without_alias"
        if instance_kind == "new_with_affinity":
            instance._state.db = "unsaved_affinity"

    assert get_write_alias(write_model, bound=unbound_owner, instance=instance) == "writer"
    assert write_router.calls == [(write_model, {"instance": instance})]


def test_unrouted_model_keeps_django_default(write_model: type[models.Model], monkeypatch: pytest.MonkeyPatch) -> None:
    """A single-database deployment retains Django's native default fallback."""

    monkeypatch.setattr(router, "routers", [])

    assert get_write_alias(write_model) == DEFAULT_DB_ALIAS
