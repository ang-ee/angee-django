"""Native Django behavior of emitted models, including the composition boundaries."""

from __future__ import annotations

import sys
from types import ModuleType

import pytest
import reversion
from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured
from django.db import connection, models
from django.db.migrations.state import ModelState
from django.test.utils import isolate_apps

from angee.base.mixins import HistoryMixin, RevisionMixin, SqidMixin
from angee.compose.model_composition import ModelComposition
from angee.compose.rendering import render_models


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
        composition.validate_concrete(
            getattr(emitted[source._meta.app_label], source.__name__) for source in composition.ordered_models
        )
        return emitted

    return create, emit


def source(module, name, label, *, bases=(models.Model,), meta=None, **body):
    model = type(
        name,
        bases,
        {
            "__module__": module.__name__,
            "Meta": type("Meta", (), {"abstract": True, "app_label": label, **(meta or {})}),
            **body,
        },
    )
    setattr(module, name, model)
    return model


@isolate_apps()
def test_native_donor_and_child_mro_fields_managers_and_meta(modules):
    create, emit = modules
    config, module = create("native_mro")

    class ParentManager(models.Manager):
        pass

    class ChildQuerySet(models.QuerySet):
        def active(self):
            return self.filter(child="active")

    Parent = source(
        module,
        "Parent",
        config.label,
        runtime=True,
        title=models.CharField(max_length=32),
        objects=ParentManager(),
        description=lambda self: "parent",
        meta={"ordering": ("title",)},
    )
    Child = source(
        module,
        "Child",
        config.label,
        runtime=True,
        extends=f"{config.label}.Parent",
        child=models.CharField(max_length=32),
        objects=ChildQuerySet.as_manager(),
        description=lambda self: "child",
        meta={"ordering": ("title",)},
    )
    Donor = source(
        module,
        "Donor",
        config.label,
        extends=f"{config.label}.Child",
        extra=models.IntegerField(default=0),
        description=lambda self: "donor",
        meta={
            "constraints": [models.CheckConstraint(condition=models.Q(extra__gte=0), name="native_extra_nonnegative")]
        },
    )
    composition = ModelComposition.discover((config,))
    concrete = emit(composition)[config.label]
    assert concrete.Child.__bases__ == (Donor, Child, concrete.Parent)
    assert concrete.Child().description() == "donor"
    assert concrete.Child._meta.parents == {concrete.Parent: concrete.Child._meta.get_field("parent_ptr")}
    assert {field.name for field in concrete.Child._meta.local_fields} == {"parent_ptr", "child", "extra"}
    assert concrete.Child._meta.get_field("title").model is concrete.Parent
    assert type(concrete.Parent._default_manager) is ParentManager
    assert isinstance(concrete.Child._default_manager.all(), ChildQuerySet)
    assert concrete.Child._meta.ordering == ("title",)
    assert [constraint.name for constraint in concrete.Child._meta.constraints] == ["native_extra_nonnegative"]
    state = ModelState.from_model(concrete.Child)
    assert state.bases == (f"{config.label}.parent", models.Model)
    assert set(state.fields) == {"parent_ptr", "child", "extra"}
    assert composition.models_by_label[f"{config.label}.parent"] is Parent


@isolate_apps()
def test_plain_abstract_declarations_are_selected_without_inherited_markers(modules):
    create, _emit = modules
    config, module = create("native_select")
    Root = source(module, "Root", config.label, runtime=True, value=models.IntegerField())
    Helper = source(module, "Helper", config.label, bases=(Root,))
    Donor = source(module, "Donor", config.label, extends=f"{config.label}.Root")
    source(module, "DonorHelper", config.label, bases=(Donor,))
    module.Alias = Donor
    composition = ModelComposition.discover((config,))
    assert composition.ordered_models == (Root,)
    assert composition.donors(Root) == (Donor,)
    assert Helper not in composition.ordered_models


@isolate_apps()
def test_donor_class_keeps_its_own_body_and_cooperative_super(modules):
    create, emit = modules
    config, module = create("native_body")
    exec(
        """
from django.db import models
class Common(models.Model):
    def describe(self):
        return ["common"]
    class Meta:
        abstract = True
        app_label = "native_body"
class Root(Common):
    runtime = True
    class Meta:
        abstract = True
        app_label = "native_body"
class Donor(Common):
    extends = "native_body.Root"
    @property
    def marker(self):
        return "donor"
    def describe(self):
        return ["donor", *super().describe()]
    class Meta:
        abstract = True
        app_label = "native_body"
""",
        vars(module),
    )
    composition = ModelComposition.discover((config,))
    Root = emit(composition)[config.label].Root
    assert Root.__bases__ == (module.Donor, module.Root)
    assert Root().describe() == ["donor", "common"]
    assert Root().marker == "donor"


@isolate_apps()
def test_composition_rejects_additive_field_collisions_and_shared_parent_fields(modules):
    create, _emit = modules
    config, module = create("native_fields")
    Root = source(module, "Root", config.label, runtime=True, value=models.IntegerField())
    source(module, "Donor", config.label, extends=f"{config.label}.Root", value=models.IntegerField())
    with pytest.raises(ImproperlyConfigured, match="composes field 'value'"):
        ModelComposition.discover((config,))
    del module.Donor
    source(module, "Child", config.label, bases=(Root,), runtime=True, extends=f"{config.label}.Root")
    with pytest.raises(ImproperlyConfigured, match="redeclares parent field 'value'"):
        ModelComposition.discover((config,))


@isolate_apps()
def test_shared_abstract_fields_are_one_declaration(modules):
    create, emit = modules
    config, module = create("native_shared")
    Common = source(module, "Common", config.label, value=models.IntegerField())
    source(module, "Root", config.label, bases=(Common,), runtime=True)
    source(module, "Donor", config.label, bases=(Common,), extends=f"{config.label}.Root")
    concrete = emit(ModelComposition.discover((config,)))[config.label].Root
    assert [field.name for field in concrete._meta.local_fields] == ["id", "value"]


@isolate_apps()
def test_cross_app_parent_cycle_fails_before_rendering(modules):
    create, _emit = modules
    left, left_module = create("native_left")
    right, right_module = create("native_right")
    source(left_module, "Left", left.label, runtime=True, extends=f"{right.label}.Right")
    source(right_module, "Right", right.label, runtime=True, extends=f"{left.label}.Left")
    with pytest.raises(ImproperlyConfigured, match="Cyclic materialized model parents"):
        ModelComposition.discover((left, right))


@isolate_apps()
def test_final_composed_transition_metadata_is_validated(modules):
    create, emit = modules
    config, module = create("native_transitions")
    exec(
        """
from django.db import models
from angee.base.fields import StateField
from angee.base.transitions import StateTransitions, transition, save_state
class Status(models.TextChoices):
    DRAFT = "draft", "Draft"
    DONE = "done", "Done"
class Root(models.Model):
    runtime = True
    status = StateField(choices_enum=Status, default=Status.DRAFT)
    transitions = StateTransitions(status, {Status.DRAFT: [Status.DONE]})
    @transition(status, source=Status.DRAFT, target=Status.DONE, on_success=save_state)
    def finish(self):
        pass
    class Meta:
        abstract = True
        app_label = "native_transitions"
class Child(models.Model):
    runtime = True
    extends = "native_transitions.Root"
    class Meta:
        abstract = True
        app_label = "native_transitions"
""",
        vars(module),
    )
    composition = ModelComposition.discover((config,))
    result = emit(composition)[config.label]
    assert result.Child.finish is result.Root.finish


@isolate_apps()
def test_cooperative_resource_hooks_visit_shared_ancestry_once(modules):
    create, emit = modules
    config, module = create("native_hooks")
    module.events = []
    exec(
        """
from angee.resources.mixins import ResourceLoadMixin
class Shared(ResourceLoadMixin):
    @classmethod
    def after_resource_load(cls, instances, **options):
        events.append(("shared", cls, tuple(instances), options))
        super().after_resource_load(instances, **options)
    class Meta:
        abstract = True
        app_label = "native_hooks"
class Root(Shared):
    runtime = True
    class Meta:
        abstract = True
        app_label = "native_hooks"
class ZFirst(Shared):
    extends = "native_hooks.Root"
    @classmethod
    def after_resource_load(cls, instances, **options):
        events.append(("first", cls, tuple(instances), options))
        super().after_resource_load(instances, **options)
    class Meta:
        abstract = True
        app_label = "native_hooks"
class ASecond(Shared):
    extends = "native_hooks.Root"
    @classmethod
    def after_resource_load(cls, instances, **options):
        events.append(("second", cls, tuple(instances), options))
        super().after_resource_load(instances, **options)
    class Meta:
        abstract = True
        app_label = "native_hooks"
class Child(ResourceLoadMixin):
    runtime = True
    extends = "native_hooks.Root"
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
    Child.after_resource_load(["row"], tier="demo", source="test", publish=True)
    assert [event[0] for event in module.events] == ["child", "first", "second", "shared"]
    assert all(event[1] is Child and event[2] == ("row",) for event in module.events)
    assert "after_resource_load" not in render_models(composition, config.label)


@pytest.mark.django_db(transaction=True)
def test_native_history_saves_virtual_fields_and_preserves_parent_tracking(modules):
    create, emit = modules
    config, module = create("native_history")
    source(
        module,
        "Tracked",
        config.label,
        bases=(SqidMixin, HistoryMixin, models.Model),
        runtime=True,
        title=models.CharField(max_length=32),
    )
    source(
        module,
        "Child",
        config.label,
        runtime=True,
        extends=f"{config.label}.Tracked",
        extra=models.IntegerField(default=0),
    )
    composition = ModelComposition.discover((config,))
    generated = emit(composition)[config.label]
    Tracked = generated.Tracked
    history = Tracked.history.model
    assert history._meta.app_label == config.label
    assert history._meta.db_table == f"{config.label}_historicaltracked"
    assert not hasattr(generated, "HistoricalChild")
    assert generated.Child.history.model is history
    assert "sqid" not in {field.name for field in history._meta.local_fields}
    with connection.schema_editor() as editor:
        editor.create_model(Tracked)
        editor.create_model(history)
    try:
        record = Tracked.objects.create(title="first")
        record.title = "second"
        record.save()
        assert list(record.history.values_list("title", flat=True)) == ["second", "first"]
        assert record.history.latest().instance.title == "second"
        record.delete()
        assert history.objects.count() == 3
    finally:
        with connection.schema_editor() as editor:
            editor.delete_model(history)
            editor.delete_model(Tracked)


@isolate_apps()
def test_native_reversion_registration_uses_final_model_fields(modules):
    create, emit = modules
    config, module = create("native_revisions")
    source(
        module,
        "Document",
        config.label,
        bases=(RevisionMixin, models.Model),
        runtime=True,
        revisioned_fields=("body",),
        body=models.TextField(),
    )
    source(module, "Child", config.label, runtime=True, extends=f"{config.label}.Document")
    generated = emit(ModelComposition.discover((config,)))[config.label]
    try:
        assert reversion.is_registered(generated.Document)
        assert not reversion.is_registered(generated.Child)
        from reversion.revisions import _get_options

        assert _get_options(generated.Document).fields == ("body",)
    finally:
        reversion.unregister(generated.Document)


@isolate_apps()
def test_acyclic_models_with_cyclic_generated_modules_fail_before_rendering(modules):
    create, _emit = modules
    left, left_module = create("native_module_left")
    right, right_module = create("native_module_right")
    source(left_module, "A1", left.label, runtime=True)
    source(right_module, "B1", right.label, runtime=True)
    source(left_module, "A2", left.label, runtime=True, extends=f"{right.label}.B1")
    source(right_module, "B2", right.label, runtime=True, extends=f"{left.label}.A1")
    source(left_module, "A3", left.label, runtime=True, extends=f"{right.label}.B2")
    source(right_module, "B3", right.label, runtime=True, extends=f"{left.label}.A2")
    with pytest.raises(ImproperlyConfigured, match="Cyclic generated model modules"):
        ModelComposition.discover((left, right))


@isolate_apps()
def test_final_composition_rejects_a_donor_transition_outside_the_owner_graph(modules):
    create, emit = modules
    config, module = create("native_bad_transition")
    exec(
        """
from django.db import models
from angee.base.fields import StateField
from angee.base.transitions import StateTransitions, transition
class Status(models.TextChoices):
    DRAFT = "draft", "Draft"
    DONE = "done", "Done"
    PAUSED = "paused", "Paused"
class Root(models.Model):
    runtime = True
    status = StateField(choices_enum=Status, default=Status.DRAFT)
    transitions = StateTransitions(status, {Status.DRAFT: [Status.DONE]})
    class Meta:
        abstract = True
        app_label = "native_bad_transition"
class Donor(models.Model):
    extends = "native_bad_transition.Root"
    @transition(Root._meta.get_field("status"), source=Status.DRAFT, target=Status.PAUSED)
    def pause(self):
        pass
    class Meta:
        abstract = True
        app_label = "native_bad_transition"
""",
        vars(module),
    )
    composition = ModelComposition.discover((config,))
    with pytest.raises(ImproperlyConfigured, match="paused"):
        emit(composition)


@isolate_apps()
def test_concrete_extension_donor_is_rejected_without_changing_plain_django_models(modules):
    create, _emit = modules
    config, module = create("native_concrete")
    source(module, "Ordinary", config.label, meta={"abstract": False})
    assert ModelComposition.discover((config,)).ordered_models == ()
    source(module, "Donor", config.label, meta={"abstract": False}, extends=f"{config.label}.Ordinary")
    with pytest.raises(ImproperlyConfigured, match="must be abstract"):
        ModelComposition.discover((config,))
