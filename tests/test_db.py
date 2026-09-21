"""Write-alias selection and FK reloads never consult read routing."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, nullcontext

import pytest
from django.db import DEFAULT_DB_ALIAS, connection, connections, models, router
from django.test.utils import CaptureQueriesContext, isolate_apps

from angee.base.db import get_write_alias, related_on


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


@pytest.fixture
def related_base_manager() -> str | None:
    """Use Django's implicit base manager unless a test names the scoped one."""

    return None


@pytest.fixture(params=[DEFAULT_DB_ALIAS, "related_writer"])
def related_models(
    request: pytest.FixtureRequest,
    database_alias: Callable[[str], AbstractContextManager[str]],
    related_base_manager: str | None,
) -> Iterator[tuple[type[models.Model], type[models.Model], str]]:
    """Provide native FK tables on default and explicitly bound writer aliases."""

    with isolate_apps():

        class VisibleManager(models.Manager):
            def get_queryset(self) -> models.QuerySet:
                return super().get_queryset().exclude(label="hidden")

        class RelatedTarget(models.Model):
            label = models.CharField(max_length=20)
            parent = models.ForeignKey("self", null=True, on_delete=models.CASCADE)
            objects = VisibleManager()
            all_objects = models.Manager()

            class Meta:
                app_label = "tests"
                base_manager_name = related_base_manager

        class RelatedRecord(models.Model):
            target = models.ForeignKey(RelatedTarget, null=True, on_delete=models.CASCADE)

            class Meta:
                app_label = "tests"

        with connection.schema_editor() as editor:
            editor.create_model(RelatedTarget)
            editor.create_model(RelatedRecord)
        try:
            alias_context = (
                nullcontext(DEFAULT_DB_ALIAS)
                if request.param == DEFAULT_DB_ALIAS
                else database_alias(request.param)
            )
            with alias_context as using:
                yield RelatedTarget, RelatedRecord, using
        finally:
            with connection.schema_editor() as editor:
                editor.delete_model(RelatedRecord)
                editor.delete_model(RelatedTarget)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("required", [True, False])
def test_related_on_binds_base_manager_and_eager_joins_without_replacing_cache(
    related_models: tuple[type[models.Model], type[models.Model], str],
    write_router: WriteRouter,
    required: bool,
) -> None:
    """Conflicting affinity/cache and a filtered default manager cannot reroute a reload."""

    target_model, record_model, using = related_models
    parent = target_model._base_manager.db_manager(using).create(label="parent")
    target = target_model._base_manager.db_manager(using).create(label="hidden", parent_id=parent.pk)
    assert not target_model._default_manager.db_manager(using).filter(pk=target.pk).exists()
    instance = record_model(target_id=target.pk)
    instance._state.db = "conflicting_affinity"
    field = instance._meta.get_field("target")
    stale = target_model(pk=target.pk, label="stale")
    field.set_cached_value(instance, stale)

    with CaptureQueriesContext(connections[using]) as queries:
        result = related_on(instance, "target", using=using, required=required, select_related=("parent",))
        assert result is not None
        assert result.label == "hidden"
        assert result.parent.pk == parent.pk
        assert result.parent._state.db == using
    assert len(queries) == 1
    assert result._state.db == using
    assert instance._state.db == "conflicting_affinity"
    assert field.get_cached_value(instance) is stale
    assert write_router.calls == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("related_base_manager", ["objects"])
def test_related_on_respects_a_declared_scoped_base_manager(
    related_models: tuple[type[models.Model], type[models.Model], str], write_router: WriteRouter
) -> None:
    """A base_manager_name='objects' policy remains authoritative on both aliases."""

    target_model, record_model, using = related_models
    target = target_model.all_objects.db_manager(using).create(label="hidden")
    assert target_model._meta.base_manager_name == "objects"
    instance = record_model(target_id=target.pk)
    instance._state.db = "conflicting_affinity"
    with pytest.raises(target_model.DoesNotExist):
        related_on(instance, "target", using=using)
    assert related_on(instance, "target", using=using, required=False) is None
    assert not instance._meta.get_field("target").is_cached(instance)
    assert write_router.calls == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("required", [True, False])
def test_related_on_null_fk_does_not_query(
    related_models: tuple[type[models.Model], type[models.Model], str],
    write_router: WriteRouter,
    required: bool,
) -> None:
    """Nullability is independent of whether a non-null target must exist."""

    _, record_model, using = related_models
    instance = record_model(target_id=None)
    with CaptureQueriesContext(connections[using]) as queries:
        assert related_on(instance, "target", using=using, required=required) is None
    assert len(queries) == 0
    assert not instance._meta.get_field("target").is_cached(instance)
    assert write_router.calls == []


@pytest.mark.django_db(transaction=True)
def test_related_on_missing_target_preserves_native_required_policy(
    related_models: tuple[type[models.Model], type[models.Model], str], write_router: WriteRouter
) -> None:
    """Missing rows use native DoesNotExist or optional None without read routing."""

    target_model, record_model, using = related_models
    instance = record_model(target_id=-1)
    with pytest.raises(target_model.DoesNotExist):
        related_on(instance, "target", using=using)
    assert related_on(instance, "target", using=using, required=False) is None
    assert not instance._meta.get_field("target").is_cached(instance)
    assert write_router.calls == []


@pytest.mark.django_db(transaction=True)
def test_related_on_refreshes_deferred_fk_id_on_the_selected_alias(
    related_models: tuple[type[models.Model], type[models.Model], str], write_router: WriteRouter
) -> None:
    """Fetching a deferred attname must not invoke its read-routed descriptor."""

    target_model, record_model, using = related_models
    target = target_model._base_manager.db_manager(using).create(label="target")
    record = record_model._base_manager.db_manager(using).create(target_id=target.pk)
    instance = record_model._base_manager.db_manager(using).only("pk").get(pk=record.pk)
    instance._state.db = "conflicting_affinity"
    field = instance._meta.get_field("target")
    field.set_cached_value(instance, target_model(pk=target.pk, label="stale"))
    with CaptureQueriesContext(connections[using]) as queries:
        result = related_on(instance, "target", using=using)
    assert len(queries) == 2
    assert result is not None
    assert result.pk == target.pk
    assert result._state.db == using
    assert instance._state.db == using
    assert not field.is_cached(instance)
    assert write_router.calls == []


def test_related_on_rejects_non_fk_fields(write_model: type[models.Model], write_router: WriteRouter) -> None:
    """Fail at the metadata boundary when the caller names a scalar field."""

    with pytest.raises(TypeError, match="not a forward foreign key"):
        related_on(write_model(), "id", using="writer")
    assert write_router.calls == []
