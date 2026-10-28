"""Checked parent contracts replace only native metaclass manager fallbacks."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.db import models
from django.test.utils import isolate_apps
from mypy import api as mypy_api  # noqa: F401 -- initialize the compiled plugin import graph.
from mypy.nodes import GDEF, Block, ClassDef, SymbolTable, SymbolTableNode, TypeInfo
from rebac.managers import TrackedManager
from rebac.mixins import INJECTED_BASE_MANAGER, RebacModelBase

from angee.base.typing import BaseCompositionContext


@pytest.mark.parametrize("name", ("_default_manager", "_base_manager"))
def test_required_parent_manager_preserves_explicit_native_selection(
    name: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    with isolate_apps():
        class ParentManager(models.Manager):
            pass

        class OtherManager(models.Manager):
            pass

        class Parent(models.Model):
            objects = ParentManager()

            class Meta:
                abstract = True
                app_label = "typing_managers"
                base_manager_name = "objects"

        class Fallback(models.Model, metaclass=RebacModelBase):
            objects = models.Manager()
            objects.auto_created = True

            class Meta:
                abstract = True
                app_label = "typing_managers"

        class Explicit(models.Model, metaclass=RebacModelBase):
            objects = OtherManager()

            class Meta:
                abstract = True
                app_label = "typing_managers"
                base_manager_name = "objects"

        class ExplicitMeta(models.Model, metaclass=RebacModelBase):
            alternate = OtherManager()
            alternate.auto_created = True

            class Meta:
                abstract = True
                app_label = "typing_managers"
                base_manager_name = "alternate"
                default_manager_name = "alternate"

        assert isinstance(Fallback._meta.base_manager, TrackedManager)
        assert Fallback._meta.base_manager.name == INJECTED_BASE_MANAGER
        assert Fallback._meta.default_manager.auto_created
        context = object.__new__(BaseCompositionContext)
        context.required_parents = {Fallback: Parent, Explicit: Parent, ExplicitMeta: Parent}
        context.composed = {f"{Parent.__module__}.{Parent.__qualname__}": Parent}

        def lookup(_api: object, model: type[models.Model]) -> TypeInfo:
            declaration = ClassDef(model.__name__, Block([]))
            info = TypeInfo(SymbolTable(), declaration, model.__module__)
            declaration.info = info
            if model is ExplicitMeta:
                meta = ClassDef("Meta", Block([]))
                meta_info = TypeInfo(SymbolTable(), meta, model.__module__)
                info.names["Meta"] = SymbolTableNode(GDEF, meta_info)
                meta_info.names[name[1:] + "_name"] = SymbolTableNode(GDEF, None)
            return info

        monkeypatch.setattr(context, "lookup_model_typeinfo", lookup)
        api = SimpleNamespace(fail=lambda *_args: pytest.fail("required parent disappeared"))
        assert context.get_model_manager(api, Fallback, name) is getattr(Parent._meta, name[1:])
        assert context.get_model_manager(api, Explicit, name) is getattr(Explicit._meta, name[1:])
        assert context.get_model_manager(api, ExplicitMeta, name) is getattr(ExplicitMeta._meta, name[1:])
        context.required_parents.clear()
        assert context.get_model_manager(api, Fallback, name) is getattr(Fallback._meta, name[1:])


def test_checked_parent_keeps_manager_methods_and_model_rows(tmp_path: Path) -> None:
    """A runtime fieldless capability reads the manager promised by its parent."""
    root = Path(__file__).resolve().parents[1]
    app = tmp_path / "manager_contract"
    app.mkdir()
    (app / "__init__.py").write_text("")
    (app / "models.py").write_text("""from typing import ClassVar
from django.db import models
from rebac.mixins import INJECTED_BASE_MANAGER, RebacModelBase
from angee.base.models.base import AngeeModel
from angee.base.managers import AngeeUnscopedManager
class ParentManager[T: models.Model](AngeeUnscopedManager[T]):
    def parent_method(self) -> str:
        return 'parent'
class OtherManager[T: models.Model](AngeeUnscopedManager[T]):
    def other_method(self) -> int:
        return 1
class Parent(AngeeModel):
    runtime = True
    title = models.CharField(max_length=30)
    objects = ParentManager()
    class Meta(AngeeModel.Meta):
        abstract = True
        base_manager_name = 'objects'
class Capability(models.Model, metaclass=RebacModelBase):
    requires_parent: ClassVar[str] = 'manager_contract.Parent'
    class Meta:
        abstract = True
class Child(Capability):
    runtime = True
    extends: ClassVar[str] = 'manager_contract.Parent'
    class Meta:
        abstract = True
class Explicit(Capability):
    runtime = True
    extends: ClassVar[str] = 'manager_contract.Parent'
    objects = OtherManager()
    class Meta:
        abstract = True
        base_manager_name = 'objects'
""")
    (tmp_path / "manager_settings.py").write_text(
        "from pathlib import Path\n"
        "SECRET_KEY = 'manager-typing'\n"
        "INSTALLED_APPS = ['django.contrib.contenttypes', 'django.contrib.auth', 'rebac', "
        "'angee', 'angee.base', 'manager_contract', 'angee.compose.apps.BuildComposeConfig']\n"
        "DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}\n"
        "DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'\n"
        f"COMPOSE_RUNTIME_DIR = Path({str(tmp_path / 'manager_runtime')!r})\n"
        "COMPOSE_RUNTIME_MODULE = 'manager_runtime'\n"
    )
    config = tmp_path / "mypy.ini"
    config.write_text(
        "[mypy]\nplugins = angee.base.typing\n"
        f"mypy_path = {tmp_path},{root},{root / 'addons'}\n"
        f"cache_dir = {tmp_path / 'cache'}\n"
        "ignore_missing_imports = True\n"
        "[mypy.plugins.django-stubs]\ndjango_settings_module = manager_settings\n"
    )
    source = tmp_path / "consumer.py"
    valid = """\
from typing import assert_type
from manager_contract.models import Capability, Child, Explicit, Parent
assert_type(Capability._base_manager.get_queryset().lock_if_supported().get(), Capability)
assert_type(Child._base_manager.get_queryset().lock_if_supported().get(), Child)
assert_type(Child.objects.parent_method(), str)
assert_type(Child.objects.get(), Child)
assert_type(Child.objects.get().title, str)
assert_type(Explicit._base_manager.get_queryset().lock_if_supported().get(), Explicit)
assert_type(Explicit.objects.get(), Explicit)
assert_type(Explicit._default_manager.other_method(), int)
assert_type(Child.system_queryset().lock_if_supported().get(), Child)
assert_type(Explicit.system_queryset().lock_if_supported().get(), Explicit)
"""
    source.write_text(valid)
    env = dict(os.environ, DJANGO_SETTINGS_MODULE="manager_settings")
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, (str(tmp_path), str(root), str(root / "addons"), env.get("PYTHONPATH")))
    )
    command = [sys.executable, "-m", "mypy", "--config-file", str(config), str(source)]
    positive = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True, check=False)
    assert positive.returncode == 0, positive.stdout + positive.stderr
    source.write_text(valid + """\
Child._base_manager.unknown_method()
Explicit._default_manager.parent_method()
Child.objects.get().title = {}
bad: str = Child.objects.get()
Child.system_queryset().unknown_method()
""")
    negative = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True, check=False)
    assert negative.returncode == 1, negative.stdout + negative.stderr
    assert "Found 5 errors in 1 file" in negative.stdout, negative.stdout
