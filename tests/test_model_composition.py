"""Angee history and resource contracts over native emitted models."""
from __future__ import annotations

import sys
from types import ModuleType

import pytest
from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.db.migrations.autodetector import MigrationAutodetector
from django.db.migrations.operations import CreateModel
from django.db.migrations.state import ModelState, ProjectState
from django.test.utils import isolate_apps
from django_compose.model_composition import ModelComposition
from django_compose.models import Composable
from django_compose.rendering import render_models

from angee.base.models.identity import SqidMixin
from angee.provenance.contrib.angee.history import HistoryMixin
from angee.provenance.contrib.angee.managers import AngeeHistoryUnscopedManager
from tests.tables import model_tables


class HistorySource(HistoryMixin, Composable):
    """Plain Django tracking declares its capture manager at the model owner."""

    objects = AngeeHistoryUnscopedManager()

    class Meta:
        abstract = True
        app_label = "tests"
        base_manager_name = "objects"



@pytest.fixture
def modules(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Provide real importable source and generated modules for compiler tests."""

    def create(label: str) -> tuple[AppConfig, ModuleType]:
        package = ModuleType(f"native_{label}")
        package.__file__ = str(tmp_path / label / "__init__.py")
        package.__path__ = [str(tmp_path / label)]
        module = ModuleType(f"{package.__name__}.models")
        monkeypatch.setitem(sys.modules, package.__name__, package)
        monkeypatch.setitem(sys.modules, module.__name__, module)
        config = AppConfig(package.__name__, package)
        config.label = label
        config.models_module = module
        return config, module

    def emit(composition: ModelComposition) -> dict[str, ModuleType]:
        root = ModuleType("native_runtime")
        root.__path__ = []
        monkeypatch.setitem(sys.modules, root.__name__, root)
        emitted = {}
        for label in dict.fromkeys(model._meta.app_label for model in composition.ordered_models):
            package = ModuleType(f"native_runtime.{label}")
            package.__path__ = []
            module = ModuleType(f"native_runtime.{label}.models")
            monkeypatch.setitem(sys.modules, package.__name__, package)
            monkeypatch.setitem(sys.modules, module.__name__, module)
            exec(
                compile(render_models(composition, label, runtime_module="native_runtime"), module.__name__, "exec"),
                vars(module),
            )
            emitted[label] = module
        return emitted

    return create, emit



def source(module, name, label, *, bases=(Composable,), meta=None, concrete=None, extends=None, inherits=None, **body):
    keywords = {"extends": extends, "inherits": inherits}
    if concrete is not None:
        keywords["concrete"] = concrete
    model = type(
        name,
        bases,
        {
            "__module__": module.__name__,
            "Meta": type("Meta", (), {"abstract": True, "app_label": label, **(meta or {})}),
            **body,
        },
        **keywords,
    )
    setattr(module, name, model)
    return model



@isolate_apps()
def test_cooperative_resource_hooks_visit_shared_ancestry_once(modules):
    create, emit = modules
    config, module = create("native_hooks")
    module.events = []
    exec(
        """
from angee.resources.mixins import ResourceLoadMixin
from django_compose.models import Composable
class Shared(ResourceLoadMixin, Composable):
    @classmethod
    def after_resource_load(cls, instances, **options):
        events.append(("shared", cls, tuple(instances), options))
        super().after_resource_load(instances, **options)
    class Meta:
        abstract = True
        app_label = "native_hooks"
class Root(Shared, concrete=True):
    class Meta:
        abstract = True
        app_label = "native_hooks"
class ZFirst(Shared, extends="native_hooks.Root"):
    @classmethod
    def after_resource_load(cls, instances, **options):
        events.append(("first", cls, tuple(instances), options))
        super().after_resource_load(instances, **options)
    class Meta:
        abstract = True
        app_label = "native_hooks"
class ASecond(Shared, extends="native_hooks.Root"):
    @classmethod
    def after_resource_load(cls, instances, **options):
        events.append(("second", cls, tuple(instances), options))
        super().after_resource_load(instances, **options)
    class Meta:
        abstract = True
        app_label = "native_hooks"
class Child(ResourceLoadMixin, Composable, inherits="native_hooks.Root"):
    @classmethod
    def after_resource_load(cls, instances, **options):
        events.append(("child", cls, tuple(instances), options))
        super().after_resource_load(instances, **options)
    class Meta:
        abstract = True
        app_label = "native_hooks"
""",
        vars(module),
    )
    composition = ModelComposition.discover((config,))
    Child = emit(composition)[config.label].Child
    Child.after_resource_load(["row"], tier="demo", source="test")
    assert [event[0] for event in module.events] == ["child", "first", "second", "shared"]
    assert all(event[1] is Child and event[2] == ("row",) for event in module.events)
    assert all(event[3] == {"tier": "demo", "source": "test"} for event in module.events)
    assert "after_resource_load" not in render_models(composition, config.label)



@isolate_apps()
def test_native_history_change_reason_fields_belong_to_each_historical_model(modules):
    create, emit = modules
    config, module = create("native_history_reasons")
    for name in ("First", "Second"):
        source(module, name, config.label, bases=(HistorySource,), concrete=True)

    generated = emit(ModelComposition.discover((config,)))[config.label]
    histories = (generated.First.history.model, generated.Second.history.model)
    reasons = [history._meta.get_field("history_change_reason") for history in histories]

    assert reasons[0] is not reasons[1]
    for history, reason in zip(histories, reasons, strict=True):
        assert reason.model is history
        assert isinstance(reason, models.TextField)
        assert reason.null is True



@isolate_apps()
def test_native_history_excludes_generated_fields_from_final_model(modules):
    create, emit = modules
    config, module = create("native_generated_history")

    class BooleanGeneratedField(models.GeneratedField):
        pass

    source(
        module,
        "Tracked",
        config.label,
        bases=(HistorySource,),
        meta={"base_manager_name": "objects"},
        concrete=True,
        value=models.IntegerField(),
        doubled=models.GeneratedField(
            expression=models.F("value") * 2,
            output_field=models.IntegerField(),
            db_persist=True,
        ),
    )
    source(
        module,
        "TrackedExtension",
        config.label,
        extends=f"{config.label}.Tracked",
        positive=BooleanGeneratedField(
            expression=models.Q(value__gt=0),
            output_field=models.BooleanField(),
            db_persist=True,
        ),
    )
    generated = emit(ModelComposition.discover((config,)))[config.label]
    Tracked = generated.Tracked
    history = Tracked.history.model

    assert generated.HistoricalTracked is history
    assert not history._meta.abstract
    assert {field.name for field in Tracked._meta.local_fields if isinstance(field, models.GeneratedField)} == {
        "doubled",
        "positive",
    }
    assert isinstance(Tracked._meta.get_field("positive"), BooleanGeneratedField)
    historical_fields = {field.name for field in history._meta.local_fields}
    assert {"id", "value"} <= historical_fields
    assert {"doubled", "positive"}.isdisjoint(historical_fields)
    assert not any(isinstance(field, models.GeneratedField) for field in history._meta.local_fields)
    assert {field.name for field in history.tracked_fields} == {"id", "value"}



@pytest.mark.django_db(transaction=True)
def test_native_history_saves_virtual_fields_and_preserves_parent_tracking(modules):
    create, emit = modules
    config, module = create("native_history")
    source(
        module,
        "Tracked",
        config.label,
        bases=(SqidMixin, HistorySource),
        meta={"base_manager_name": "objects"},
        concrete=True,
        title=models.CharField(max_length=32),
    )
    source(
        module,
        "Child",
        config.label,
        inherits=f"{config.label}.Tracked",
        objects=AngeeHistoryUnscopedManager(),
        meta={"base_manager_name": "objects"},
        extra=models.IntegerField(default=0),
    )
    composition = ModelComposition.discover((config,))
    generated = emit(composition)[config.label]
    Tracked = generated.Tracked
    history = Tracked.history.model
    assert history._meta.app_label == config.label
    assert history._meta.db_table == f"{config.label}_historicaltracked"
    assert generated.Child.history.model is generated.HistoricalChild
    assert generated.Child.history.model is not history
    assert "sqid" not in {field.name for field in history._meta.local_fields}
    with model_tables((Tracked, generated.Child, history, generated.HistoricalChild)):
        record = Tracked.objects.create(title="first")
        record.title = "second"
        record.save()
        assert list(record.history.values_list("title", flat=True)) == ["second", "first"]
        assert record.history.latest().instance.title == "second"
        record.delete()
        assert history.objects.count() == 3



@isolate_apps()
def test_native_history_mti_reference_has_a_migration_dependency(modules):
    create, emit = modules
    config, module = create("native_history_mti")
    source(
        module,
        "Tracked",
        config.label,
        bases=(HistorySource,),
        concrete=True,
        title=models.CharField(max_length=32),
    )
    source(
        module,
        "Child",
        config.label,
        bases=(HistorySource,),
        inherits=f"{config.label}.Tracked",
        extra=models.IntegerField(default=0),
    )
    generated = emit(ModelComposition.discover((config,)))[config.label]
    history = generated.Child.history.model
    parent_reference = history._meta.get_field("tracked_ptr")

    assert parent_reference.auto_created is False
    assert parent_reference.remote_field.parent_link is False
    assert parent_reference.target_field.name == "id"
    snapshot = history(
        tracked_ptr_id=7,
        id=7,
        title="parent",
        extra=2,
        history_user_id=None,
    )
    assert snapshot.instance.pk == 7
    assert snapshot.instance.extra == 2

    models_to_migrate = (generated.Tracked, generated.Child, history)
    target = ProjectState()
    for model in models_to_migrate:
        state = ModelState.from_model(model)
        if state.name == "HistoricalChild":
            state.fields.pop("history_user")
            state.fields.pop("changeset")
        target.add_model(state)
    changes = MigrationAutodetector(ProjectState(), target)._detect_changes()
    operations = changes[config.label][0].operations
    parent_create = next(
        index
        for index, operation in enumerate(operations)
        if isinstance(operation, CreateModel) and operation.name == "Tracked"
    )
    history_create = next(
        index
        for index, operation in enumerate(operations)
        if isinstance(operation, CreateModel) and operation.name == "HistoricalChild"
    )

    assert parent_create < history_create
    rendered = ProjectState()
    for operation in operations:
        operation.state_forwards(config.label, rendered)
    rendered.apps.get_model(config.label, "HistoricalChild")



@isolate_apps()
def test_native_history_refuses_an_explicit_uncaptured_base_manager(modules):
    create, emit = modules
    config, module = create("native_uncaptured_history")
    source(
        module, "Uncaptured", config.label, bases=(HistoryMixin, Composable), concrete=True,
        objects=models.Manager(), meta={"base_manager_name": "objects"},
    )
    with pytest.raises(ImproperlyConfigured, match="history-capable base manager"):
        emit(ModelComposition.discover((config,)))



@isolate_apps()
def test_native_history_refuses_an_uncaptured_default_manager_with_a_captured_base(modules):
    create, emit = modules
    config, module = create("native_uncaptured_default_history")
    source(
        module, "UncapturedDefault", config.label, bases=(HistoryMixin, Composable), concrete=True,
        objects=models.Manager(), _angee_base=AngeeHistoryUnscopedManager(),
        meta={"base_manager_name": "_angee_base", "default_manager_name": "objects"},
    )
    with pytest.raises(ImproperlyConfigured, match="history-capable default manager"):
        emit(ModelComposition.discover((config,)))
