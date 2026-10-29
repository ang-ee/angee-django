"""Native Django metadata retained by the framework's typing adapter."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.core.exceptions import FieldError
from django.db import models
from django.test.utils import isolate_apps
from mypy import api as mypy_api  # noqa: F401 -- initialize mypy's compiled import graph before its plugin API.
from mypy.mro import calculate_mro
from mypy.nodes import GDEF, Block, ClassDef, NameExpr, SymbolTable, SymbolTableNode, TypeInfo
from mypy.plugin import ClassDefContext
from mypy.types import Instance

from angee.compose.typing import CompositionContext, CompositionPlugin


@pytest.mark.parametrize("settings_module", ("tests.typing_settings", "tests.typing_app.settings"))
def test_composition_context_discovers_native_registry_without_compiler_app(settings_module: str) -> None:
    """Prebuilt and ordinary Django hosts need no installed compiler AppConfig."""

    root = Path(__file__).resolve().parents[1]
    env = {
        name: value for name, value in os.environ.items()
        if not name.startswith(("ANGEE_", "COMPOSE_")) and name not in {"DATABASE_URL", "DJANGO_SETTINGS_MODULE"}
    }
    env["PYTHONPATH"] = str(root)
    result = subprocess.run(
        [sys.executable, "-c", "\n".join([
            # Initialize compiled mypy's import graph before importing its plugin API.
            "from mypy import api",
            "from angee.compose.typing import CompositionContext",
            f"context = CompositionContext({settings_module!r})",
            "assert not context.apps_registry.is_installed('angee.compose')",
            "if context.django_settings_module == 'tests.typing_app.settings':",
            "    assert not hasattr(context.settings, 'COMPOSE_RUNTIME_DIR')",
            "    assert not context.composed",
            "    assert not context.static_names",
            "    assert not context.extends",
            "    assert not context.contributions",
            "    assert not context.required_parents",
            "else:",
            "    queue = context.apps_registry.get_model('work', 'Queue')",
            "    assert context.composed['angee.work.models.Queue'] is queue",
            "    assert context.get_model_class_by_fullname('angee.work.models.Queue') is queue",
            "    assert context.static_names[queue] == 'angee.work.models.Queue'",
            "    assert context.extends['angee.work.models.Queue'] == 'angee.spaces.models.Group'",
            "    assert context.extends['angee.integrate.models.Bridge'] == 'angee.integrate.models.Integration'",
        ])], cwd=root, env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_abstract_self_fk_projection_has_the_same_unknown_pk_as_native_pk() -> None:
    with isolate_apps():
        class AbstractTree(models.Model):
            parent = models.ForeignKey("self", null=True, on_delete=models.PROTECT)
            count = models.IntegerField()

            class Meta:
                abstract = True
                app_label = "typing_contract"

        context = object.__new__(CompositionContext)
        assert context.resolve_lookup_into_field(AbstractTree, "pk") == (None, AbstractTree)
        assert context.resolve_lookup_into_field(AbstractTree, "parent_id") == (None, AbstractTree)
        assert context.solve_lookup_type(AbstractTree, "parent_id__in") is None
        assert context.solve_lookup_type(AbstractTree, "parent__isnull") is not None
        field, owner = context.resolve_lookup_into_field(AbstractTree, "count")
        assert field is AbstractTree._meta.get_field("count")
        assert owner is AbstractTree
        with pytest.raises(FieldError):
            context.resolve_lookup_into_field(AbstractTree, "missing_field")


def test_concrete_self_fk_retains_native_relation_and_primary_key_metadata() -> None:
    with isolate_apps():
        class Tree(models.Model):
            parent = models.ForeignKey("self", null=True, on_delete=models.PROTECT)
            count = models.IntegerField()

            class Meta:
                app_label = "typing_contract"

        context = object.__new__(CompositionContext)
        field, owner = context.resolve_lookup_into_field(Tree, "parent_id")
        assert field is Tree._meta.get_field("parent")
        assert owner is Tree
        assert context.get_primary_key_field(Tree) is Tree._meta.pk



def test_composition_target_survives_semantic_reanalysis(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mypy restores source bases while the previous pass's MRO is still cached."""
    def model_info(name: str) -> TypeInfo:
        declaration = ClassDef(name, Block([]))
        declaration.fullname = f"typing_contract.{name}"
        info = TypeInfo(SymbolTable(), declaration, "typing_contract")
        declaration.info = info
        return info

    root = model_info("object")
    root.mro = [root]
    target = model_info("Target")
    target.bases = [Instance(root, [])]
    calculate_mro(target)
    donor = model_info("Donor")
    donor.bases = [Instance(root, [])]
    calculate_mro(donor)
    context = object.__new__(CompositionContext)
    context.extends = {donor.fullname: target.fullname}
    context.contributions = {}
    context.member_origins = {}
    context.member_receivers = {}
    context.member_modules = {}
    plugin = CompositionPlugin.__new__(CompositionPlugin)
    plugin.django_context = context
    monkeypatch.setattr(plugin, "lookup_fully_qualified", lambda _name: SymbolTableNode(GDEF, target))
    ctx = ClassDefContext(
        donor.defn, NameExpr("source_base"),
        SimpleNamespace(add_plugin_dependency=lambda *_args, **_kwargs: None),
    )
    native_calls: list[ClassDefContext] = []
    plugin._composition_contract(ctx, native=native_calls.append)
    assert donor.has_base(target.fullname)
    donor.bases = [Instance(root, [])]
    assert donor.has_base(target.fullname)  # The preceding MRO has not been rebuilt yet.
    plugin._composition_contract(ctx, native=native_calls.append)
    assert any(base.type is target for base in donor.bases)
    assert donor.has_base(target.fullname)
    assert native_calls == [ctx, ctx]



def test_system_queryset_preserves_configured_classes_and_scoped_locks() -> None:
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, DJANGO_SETTINGS_MODULE="tests.typing_app.settings")
    result = subprocess.run(
        [sys.executable, "-c", "\n".join([
            "import django; django.setup()",
            "from django.db.models import QuerySet",
            "from tests.typing_app.models import SystemRecord, SystemRecordQuerySet, PlainSystemRecord",
            "custom = SystemRecord.system_queryset(lock=('self',))",
            "assert type(custom) is SystemRecordQuerySet",
            "assert custom.query.select_for_update",
            "assert custom.query.select_for_update_of == ('self',)",
            "plain = PlainSystemRecord.system_queryset(lock=('self',))",
            "assert type(plain) is QuerySet",
            "assert plain.query.select_for_update",
            "assert plain.query.select_for_update_of == ('self',)",
        ])], cwd=root, env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_native_plugin_rejects_invalid_fields_relations_and_manager_methods(tmp_path: Path) -> None:
    """The abstract-PK adapter must preserve native concrete-model rejection."""
    root = Path(__file__).resolve().parents[1]
    config = root / "tests/typing_app/mypy.ini"
    valid = tmp_path / "valid.py"
    valid.write_text(
        "from typing import assert_type\n"
        "from tests.typing_app.models import AbstractTree, Tree, FrameworkRecord, factory_result\n"
        "assert_type(factory_result, str)\n"
        "row = Tree(name='valid', count=1)\n"
        "row.count = 2\n"
        "row.parent = Tree(name='parent', count=1)\n"
        "Tree.objects.get(count=1)\n"
        "Tree.objects.values_list('count', flat=True)\n"
        "AbstractTree.parent_ids()\n"
        "assert_type(FrameworkRecord.objects.get(), FrameworkRecord)\n"
        "assert_type(FrameworkRecord.objects.get().title, str)\n"
        "assert_type(FrameworkRecord.objects.get_queryset().get(), FrameworkRecord)\n"
        "assert_type(FrameworkRecord.objects.get_queryset().lock_if_supported().get(), FrameworkRecord)\n"
        "from angee.base.implementations import ImplBase\nfrom angee.base.fields import ImplClassField\n"
        "class Implementation(ImplBase):\n"
        "    registry_setting = 'ANGEE_TYPING_IMPL_CLASSES'\n"
        "    known = True\n"
        "ImplClassField(Implementation).resolve_class('impl').known\n"
        "from tests.typing_app.models import ImplementationRecord\n"
        "from tests.typing_app.models import TypingImplementation\n"
        "assert_type(ImplementationRecord().get_backend_class_impl(), type[TypingImplementation])\n"
        "assert_type(ImplementationRecord().get_backend_class_impl(default='local'), type[TypingImplementation])\n"
        "from tests.typing_app.factory import OtherFactory\n"
        "assert_type(OtherFactory.from_queryset(1), str)\n"
        "from angee.iam.models import User, UserKind, UserQuerySet\n"
        "from angee.parties.models import Party\n"
        "from angee.parties.managers import PartyManager, PartyQuerySet\n"
        "assert_type(User.objects.get(), User)\n"
        "assert_type(User.objects.get().username, str)\n"
        "assert_type(User.objects.get_queryset(), UserQuerySet[User])\n"
        "assert_type(User.objects.people(), UserQuerySet[User])\n"
        "assert_type(User.objects.active_people(), UserQuerySet[User])\n"
        "assert_type(UserKind.PERSON.value, str)\n"
        "assert_type(User.objects.get_by_natural_key('name'), User)\n"
        "assert_type(User.objects.get_for_session('id'), User)\n"
        "assert_type(User.objects.person_for_email('mail@example.test'), User | None)\n"
        "assert_type(User.objects.create_user('name'), User)\n"
        "assert_type(User.objects.create_superuser('name'), User)\n"
        "assert_type(User.objects.create_person('name'), User)\n"
        "assert_type(User.objects.create_person_as_system('name', 'mail@example.test', reason='typing'), User)\n"
        "assert_type(User.objects.visible_people(None), list[User])\n"
        "assert_type(User.objects.viewable_people(None), list[User])\n"
        "assert_type(Party.objects.get(), Party)\n"
        "assert_type(Party.objects.get_queryset(), PartyQuerySet[Party])\n"
        "assert_type(Party.objects.get_queryset().canonical().with_circle_names().get(), Party)\n"
        "class ChildParty(Party):\n"
        "    class Meta:\n"
        "        abstract = True\n"
        "assert_type(PartyManager[ChildParty]().get_queryset().with_circle_names().get(), ChildParty)\n"
        "async def credentials() -> None:\n"
        "    assert_type(await User.objects.aget_by_natural_key('name'), User)\n"
        "from angee.provenance.context import changeset_model, current_changeset, require_changeset\n"
        "from tests.typing_app.models import TypingChangeset\n"
        "assert_type(changeset_model(), type[TypingChangeset])\n"
        "assert_type(current_changeset(), TypingChangeset | None)\n"
        "assert_type(current_changeset(required=True), TypingChangeset | None)\n"
        "assert_type(require_changeset().note, str)\n"
        "assert_type(changeset_model().objects.get().note, str)\n"
        "from angee.parties.models import Handle\n"
        "from angee.parties.managers import HandleQuerySet\n"
        "assert_type(Handle.objects.get(), Handle)\n"
        "assert_type(Handle.objects.get_queryset(), HandleQuerySet[Handle])\n"
        "named = Handle.objects.with_sender_name()\n"
        "assert_type(named.get()._sender_name, str)\n"
        "from django.db.models import QuerySet\n"
        "from tests.typing_app.models import PlainSystemRecord, SystemRecord, SystemRecordQuerySet\n"
        "assert_type(SystemRecord.system_queryset(), SystemRecordQuerySet[SystemRecord])\n"
        "assert_type(SystemRecord.system_queryset(lock=('self',)).selected().get(), SystemRecord)\n"
        "assert_type(PlainSystemRecord.system_queryset(), QuerySet[PlainSystemRecord])\n"
        "class OtherSystemOwner:\n"
        "    @classmethod\n"
        "    def system_queryset(cls) -> str:\n"
        "        return 'unrelated'\n"
        "assert_type(OtherSystemOwner.system_queryset(), str)\n"
        "def system_row[RecordT: SystemRecord](model: type[RecordT]) -> RecordT:\n"
        "    assert_type(model.system_queryset(), SystemRecordQuerySet[RecordT])\n"
        "    return model.system_queryset().selected().get()\n"
        "from angee.provenance.history import HistoryMixin\n"
        "def history_write(record: HistoryMixin) -> None:\n"
        "    record.save_without_historical_record(force_update=False, using='default', update_fields=['name'])\n"
        "from angee.integrate.models import Bridge\n"
        "from angee.integrate.registry import models_with\n"
        "assert_type(models_with(base=Bridge), tuple[type[Bridge], ...])\n"
    )
    invalid = tmp_path / "invalid.py"
    invalid.write_text(
        "from tests.typing_app.models import FrameworkRecord, Other, Tree\n"
        "row = Tree(name='invalid', count={})\n"
        "row.count = {}\n"
        "row.parent = Other(name='wrong relation')\n"
        "Tree.objects.missing_method()\n"
        "Tree.objects.values_list('missing_field', flat=True)\n"
        "from angee.base.implementations import ImplBase\nfrom angee.base.fields import ImplClassField\n"
        "class Implementation(ImplBase):\n"
        "    registry_setting = 'ANGEE_TYPING_IMPL_CLASSES'\n"
        "ImplClassField(Implementation).resolve_class('impl').missing\n"
        "from tests.typing_app.models import ImplementationRecord\n"
        "ImplementationRecord().get_backend_class_impl().missing\n"
        "ImplementationRecord().get_backend_class_impl(default=1)\n"
        "FrameworkRecord.objects.missing_method()\n"
        "FrameworkRecord.objects.get().count = {}\n"
        "FrameworkRecord.objects.create(title='invalid', count={})\n"
        "from angee.iam.models import User\n"
        "User.objects.people().missing_method()\n"
        "User.objects.get_queryset().active_people().get().missing_field\n"
        "User.objects.get().username = {}\n"
        "from angee.parties.models import Party\n"
        "Party.objects.get_queryset().canonical().with_circle_names().missing_method()\n"
        "Party.objects.get_queryset().with_circle_names().get().missing_field\n"
        "Party.objects.get_queryset().canonical().get().display_name = {}\n"
        "from angee.provenance.context import changeset_model, current_changeset, require_changeset\n"
        "require_changeset().note = {}\n"
        "current_changeset(required=True).note\n"
        "changeset_model().objects.missing_method()\n"
        "from angee.parties.models import Handle\n"
        "bad_name: int = Handle.objects.with_sender_name().get()._sender_name\n"
        "from tests.typing_app.models import PlainSystemRecord, SystemRecord\n"
        "SystemRecord.system_queryset().selected().missing_method()\n"
        "SystemRecord.system_queryset().get().count = {}\n"
        "PlainSystemRecord.system_queryset().selected()\n"
        "from angee.provenance.history import HistoryMixin\n"
        "def bad_history(record: HistoryMixin) -> None:\n"
        "    record.save_without_historical_record(unknown=True)\n"
        "    record.save_without_historical_record(force_update='bad')\n"
    )
    env = {**os.environ, "PYTHONPATH": os.pathsep.join((str(root), str(root / "addons")))}
    command = [sys.executable, "-m", "mypy", "--config-file", str(config)]
    positive = subprocess.run(
        [*command, str(valid), str(root / "addons/angee/base/managers.py")],
        cwd=root, env=env, capture_output=True, text=True, check=False,
    )
    assert positive.returncode == 0, positive.stdout + positive.stderr
    negative = subprocess.run([*command, str(invalid)], cwd=root, env=env, capture_output=True, text=True, check=False)
    assert negative.returncode == 1, negative.stdout + negative.stderr
    for line in (2, 3, 4, 5, 6, 11, 13, 14, 15, 16, 17, 19, 20, 21, 23, 24, 25, 27, 28, 29, 31, 33, 34, 35, 38, 39):
        assert f"invalid.py:{line}: error:" in negative.stdout, negative.stdout

    (tmp_path / "disabled_storage_settings.py").write_text(
        "from tests.typing_app.settings import *\nANGEE_CHANGESET_MODEL = ''\n"
    )
    disabled_config = tmp_path / "disabled.ini"
    disabled_config.write_text(config.read_text().replace("tests.typing_app.settings", "disabled_storage_settings"))
    disabled_source = tmp_path / "disabled.py"
    disabled_source.write_text(
        "from typing import assert_type\n"
        "from django.db.models import Model\n"
        "from angee.provenance.context import ChangesetLike, changeset_model, current_changeset, require_changeset\n"
        "assert_type(changeset_model(), type[Model])\n"
        "assert_type(current_changeset(required=True), ChangesetLike | None)\n"
        "assert_type(require_changeset(), ChangesetLike)\n"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, (str(tmp_path), str(root), str(root / "addons"), env.get("PYTHONPATH")))
    )
    disabled = subprocess.run(
        [sys.executable, "-m", "mypy", "--config-file", str(disabled_config), str(disabled_source)],
        cwd=root, env=env, capture_output=True, text=True, check=False,
    )
    assert disabled.returncode == 0, disabled.stdout + disabled.stderr
