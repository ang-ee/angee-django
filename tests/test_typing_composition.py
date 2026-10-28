"""The typing view follows native composition without changing serving classes."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_SOURCE = """\
from typing import ClassVar, Self, overload
from uuid import uuid4
from django.db import models
class Target(models.Model):
    runtime = True
    key = models.UUIDField(primary_key=True, default=uuid4)
    title = models.CharField(max_length=30)
    def chain(self) -> str:
        return 'target'
    class Meta:
        abstract = True
class Shared(models.Model):
    shared = models.IntegerField()
    class Meta:
        abstract = True
class Description:
    def __get__(self, instance: object, owner: type[object] | None = None) -> str:
        return 'description'
    def __set__(self, instance: object, value: str) -> None:
        pass
class GenericMethods[T]:
    def generic_value(self, value: T) -> T:
        return value
    @classmethod
    def generic_class(cls, value: T) -> T:
        return value
class First(Shared, GenericMethods[int]):
    extends: ClassVar[str] = 'typing_composition.Target'
    first = models.IntegerField()
    described: Description = Description()
    def only_first(self) -> int:
        return self.first
    def cloned(self: Self) -> Self:
        return self
    @classmethod
    def constructed(cls) -> Self:
        return cls()
    @staticmethod
    def formatted(value: int) -> str:
        return str(value)
    @property
    def display(self) -> str:
        return self.title
    @display.setter
    def display(self, value: str) -> None:
        self.title = value
    @property
    def readonly(self) -> int:
        return self.first
    @property
    def reflected(self) -> Self:
        return self
    @overload
    def convert(self, value: int) -> int: ...
    @overload
    def convert(self, value: str) -> str: ...
    def convert(self, value: int | str) -> int | str:
        return value
    def winner(self) -> str:
        return 'first'
    def chain(self) -> str:
        return 'first-' + super().chain()
    class Meta:
        abstract = True
class Second(Shared):
    extends: ClassVar[str] = 'typing_composition.Target'
    second = models.CharField(max_length=30)
    def only_second(self) -> str:
        return self.second
    def winner(self) -> str:
        return 'second'
    def chain(self) -> str:
        return 'second-' + super().chain()
    class Meta:
        abstract = True
class Child(models.Model):
    runtime = True
    extends: ClassVar[str] = 'typing_composition.Target'
    child = models.BooleanField()
    class Meta:
        abstract = True
class Link(models.Model):
    runtime = True
    target = models.ForeignKey('typing_composition.Target', on_delete=models.CASCADE)
    targets = models.ManyToManyField('typing_composition.Target', related_name='many_links')
    class Meta:
        abstract = True
class Middle(models.Model):
    runtime = True
    extends: ClassVar[str] = 'typing_composition.Target'
    middle_value = models.IntegerField()
    class Meta:
        abstract = True
class Last(models.Model):
    extends: ClassVar[str] = 'typing_composition.Middle'
    def deepest(self) -> str:
        return self.title
    class Meta:
        abstract = True
class Capability(models.Model):
    requires_parent: ClassVar[str] = 'typing_composition.Target'
    ordinary: int
    def parent_title(self) -> str:
        return self.title
    class Meta:
        abstract = True
class CapableChild(Capability):
    runtime = True
    extends: ClassVar[str] = 'typing_composition.Middle'
    requires_parent: ClassVar[str] = 'typing_composition.Target'
    class Meta:
        abstract = True
class Ordinary(models.Model):
    count = models.IntegerField()
"""

_POSITIVE = """\
from typing import assert_type
from uuid import UUID
from typing_composition.models import Target, First, Second, Child, Link, Last, Capability, CapableChild
from composition_runtime.typing_composition.models import Target as ConcreteTarget

def proof(target: Target, first: First, second: Second, child: Child, link: Link) -> None:
    assert_type(target.only_first(), int)
    assert_type(child.only_first(), int)
    assert_type(target.only_second(), str)
    assert_type(first.only_second(), str)
    assert_type(second.only_first(), int)
    assert_type(target.cloned(), Target)
    assert_type(child.cloned(), Child)
    assert_type(Target.constructed(), Target)
    assert_type(Child.constructed(), Child)
    assert_type(target.formatted(1), str)
    assert_type(Target.formatted(1), str)
    assert_type(target.display, str)
    target.display = 'changed'
    assert_type(target.readonly, int)
    assert_type(target.reflected, Target)
    assert_type(child.reflected, Child)
    assert_type(target.convert(1), int)
    assert_type(target.convert('value'), str)
    assert_type(target.winner(), str)
    assert_type(target.described, str)
    target.described = 'changed'
    assert_type(target.generic_value(1), int)
    assert_type(child.generic_value(1), int)
    assert_type(Target.generic_class(1), int)
    assert_type(target.first, int)
    assert_type(target.shared, int)
    assert_type(target.second, str)
    assert_type(first.second, str)
    assert_type(second.first, int)
    assert_type(first.title, str)
    assert_type(first.chain(), str)
    assert_type(second.chain(), str)
    assert_type(child.title, str)
    assert_type(child.target_ptr, ConcreteTarget)
    assert_type(child.target_ptr_id, UUID)
    child.target_ptr = target
    assert_type(child.first, int)
    parent: Target = child
    link.target = target
    link.target = child
    assert_type(link.target, ConcreteTarget)
    link.targets.add(target, child)
    assert_type(list(link.targets.all()), list[Target])
    assert_type(link.targets.get().only_second(), str)
    assert_type(target.key, UUID)
    assert_type(link.target_id, UUID)
    Target.objects.filter(key=UUID(int=0))
    Link.objects.filter(target=target)

def transitive(last: Last) -> None:
    assert_type(last.title, str)
    assert_type(last.first, int)
    assert_type(last.deepest(), str)

def capability(row: Capability, child: CapableChild) -> None:
    assert_type(row.title, str)
    assert_type(row.parent_title(), str)
    assert_type(row._meta.get_field('title').max_length, int | None)
    row.save(update_fields=['title'])
    assert_type(child.title, str)
    assert_type(child.middle_value, int)
    parent: Target = child

def getter(link: Link) -> None:
    assert_type(link.target, ConcreteTarget)
    assert_type(link.target.only_first(), int)
    assert_type(link.target.only_second(), str)
from typing_composition.models import Ordinary
assert_type(Ordinary.objects.get().count, int)

from django.apps import apps
assert_type(apps.get_model('typing_composition', 'Target'), type[ConcreteTarget])
assert_type(apps.get_model('typing_composition.Target').objects.get().only_first(), int)

def authored_factory(target: Target) -> None:
    model: type[Target] = apps.get_model('typing_composition.Target')
    assert_type(model.objects.bulk_create([target]), list[Target])

def reverse(target: Target) -> None:
    assert_type(target.link_set.get().target.only_first(), int)
"""

_NEGATIVE = """\
from typing_composition.models import Target, First, Child, Link

def invalid(target: Target, first: First, child: Child, link: Link) -> None:
    target.first = {}
    first.second = {}
    link.target = link
    child.target_ptr = link
    link.targets.add(link)
    link.target_id = {}
    Target.objects.filter(unknown=1)
from typing_composition.models import Ordinary
Ordinary.objects.get().count = {}

def bad_getter(link: Link) -> None:
    link.target.missing_method()

def bad_reverse(target: Target) -> None:
    target.link_set.missing_method()
    target.link_set.get().target.second = {}
from typing_composition.models import Capability

def bad_capability(row: Capability) -> None:
    row.title = {}
    row.missing_method()
    row.save(update_fields=['missing'])
    row.save(update_fields=['ordinary'])
    row.save(update_fields=['key'])
Ordinary().save(update_fields=['missing'])

def bad_members(target: Target, child: Child) -> None:
    target.only_first(1)
    target.formatted('wrong')
    target.convert([])
    target.display = 1
    target.readonly = 1
    target.unknown_contract()
    child.only_second(1)
    target.described = 1
    target.generic_value('wrong')
    target.generic_class('wrong')
"""


def test_composition_fields_identity_and_cache_follow_native_declarations(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    app = tmp_path / "typing_composition"
    app.mkdir()
    (app / "__init__.py").write_text("")
    models = app / "models.py"
    first_module = app / "first.py"
    second_module = app / "second.py"

    def write_source(source: str) -> None:
        first_declaration = "class First(Shared, GenericMethods[int]):"
        prefix, first = source.split(first_declaration, 1)
        first, second = first.split("class Second(Shared):", 1)
        second, suffix = second.split("class Child(models.Model):", 1)
        imports = (
            "from typing import ClassVar, Self, overload\nfrom django.db import models\n"
            "from .models import Shared, Description, GenericMethods\n"
        )
        first_module.write_text(imports + first_declaration + first)
        second_module.write_text(imports + "class Second(Shared):" + second)
        models.write_text(prefix + "from .first import First as First\nfrom .second import Second as Second\n"
                          + "class Child(models.Model):" + suffix)

    write_source(_SOURCE)
    (tmp_path / "typing_composition_settings.py").write_text(
        "from pathlib import Path\n"
        "SECRET_KEY = 'composition-typing'\n"
        "INSTALLED_APPS = ['django.contrib.contenttypes', 'django.contrib.auth', 'rebac', "
        "'angee', 'typing_composition', 'angee.compose.apps.BuildComposeConfig']\n"
        "DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}\n"
        "DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'\n"
        f"COMPOSE_RUNTIME_DIR = Path({str(tmp_path / 'composition_runtime')!r})\n"
        "COMPOSE_RUNTIME_MODULE = 'composition_runtime'\n"
    )
    config = tmp_path / "mypy.ini"
    config.write_text(
        "[mypy]\nplugins = angee.compose.typing\n"
        f"mypy_path = {tmp_path},{root},{root / 'addons'}\n"
        f"cache_dir = {tmp_path / 'cache'}\n"
        "ignore_missing_imports = True\n"
        "[mypy.plugins.django-stubs]\ndjango_settings_module = typing_composition_settings\n"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(tmp_path), str(root), env.get("PYTHONPATH"))))
    env["DJANGO_SETTINGS_MODULE"] = "typing_composition_settings"
    valid = tmp_path / "valid.py"
    invalid = tmp_path / "invalid.py"
    valid.write_text(_POSITIVE)
    invalid.write_text(_NEGATIVE)
    command = [sys.executable, "-m", "mypy", "--show-traceback", "--config-file", str(config)]

    def check(path: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run([*command, str(path)], cwd=root, env=env, capture_output=True, text=True, check=False)

    for _ in range(2):
        positive = check(valid)
        assert positive.returncode == 0, positive.stdout + positive.stderr
    donor_positive = check(first_module)
    assert donor_positive.returncode == 0, donor_positive.stdout + donor_positive.stderr
    negative = check(invalid)
    assert negative.returncode == 1, negative.stdout + negative.stderr
    assert negative.stdout.count(": error:") == 27, negative.stdout
    for line in (4, 5, 6, 7, 8, 9, 10, 12, 15, 18, 19, 23, 24, 25, 26, 27, 28):
        assert f"invalid.py:{line}: error:" in negative.stdout, negative.stdout
    for line, source in enumerate(_NEGATIVE.splitlines(), 1):
        if source.startswith("    target.") or source.startswith("    child.only_second"):
            assert f"invalid.py:{line}: error:" in negative.stdout, negative.stdout

    # A warm target and its consumers must follow changed and removed donor fields.
    write_source(_SOURCE.replace("first = models.IntegerField()", "first = models.CharField(max_length=30)"))
    valid.write_text(_POSITIVE.replace("target.first, int", "target.first, str")
                     .replace("child.first, int", "child.first, str").replace("second.first, int", "second.first, str")
                     .replace("last.first, int", "last.first, str"))
    changed = check(valid)
    assert changed.returncode == 0, changed.stdout + changed.stderr
    write_source(_SOURCE.replace("    first = models.IntegerField()\n", ""))
    removed = check(valid)
    assert removed.returncode == 1, removed.stdout + removed.stderr
    assert removed.stdout.count('has no attribute "first"') == 4, removed.stdout
    write_source(_SOURCE)
    valid.write_text(_POSITIVE)

    # Only the origin module changes: target/child/FK consumers must invalidate.
    original = first_module.read_text()
    first_module.write_text(original.replace(
        "def only_first(self) -> int:\n        return self.first",
        "def only_first(self) -> str:\n        return str(self.first)",
    ))
    valid.write_text(_POSITIVE.replace("only_first(), int", "only_first(), str"))
    changed_method = check(valid)
    assert changed_method.returncode == 0, changed_method.stdout + changed_method.stderr
    first_module.write_text(original.replace("    def only_first(self) -> int:\n        return self.first\n", ""))
    removed_method = check(valid)
    assert removed_method.returncode == 1, removed_method.stdout + removed_method.stderr
    assert 'has no attribute "only_first"' in removed_method.stdout, removed_method.stdout
    first_module.write_text(original)
    added_probe = _POSITIVE + "\ndef added(target: Target) -> None:\n    assert_type(target.added_contract(), bytes)\n"
    valid.write_text(added_probe)
    absent = check(valid)
    assert absent.returncode == 1 and 'has no attribute "added_contract"' in absent.stdout, absent.stdout
    first_module.write_text(original + "\n    def added_contract(self) -> bytes:\n        return b'added'\n")
    added = check(valid)
    assert added.returncode == 0, added.stdout + added.stderr
    first_module.write_text(original.replace("def chain(self) -> str:", "def chain(self) -> int:")
                            .replace("return 'first-' + super().chain()", "return 1"))
    override = check(first_module)
    assert override.returncode == 1 and "[override]" in override.stdout, override.stdout + override.stderr
    first_module.write_text(original)
    valid.write_text(_POSITIVE)
    restored = check(valid)
    assert restored.returncode == 0, restored.stdout + restored.stderr

    native = subprocess.run(
        [sys.executable, "-c", "\n".join([
            "import django; django.setup()",
            "from django.apps import apps",
            "from django.db import models",
            "from django.db.migrations.state import ProjectState",
            "from typing_composition.models import Target, First, Second, Shared, Child, Link, Ordinary",
            "from typing_composition.models import GenericMethods",
            "from angee.compose.typing import CompositionContext",
            "before = ProjectState.from_apps(apps)",
            "context = CompositionContext('typing_composition_settings')",
            "emitted = apps.get_model('typing_composition', 'Target')",
            "child = apps.get_model('typing_composition', 'Child')",
            "assert emitted().chain() == 'first-second-target'",
            "assert emitted().winner() == 'first'",
            "winner = context.member_origins['typing_composition.models.Target']['winner']",
            "assert winner == 'typing_composition.first.First'",
            "assert issubclass(child, emitted)",
            "assert not issubclass(Child, Target)",
            "assert First.__bases__ == (Shared, GenericMethods)",
            "assert Second.__bases__ == (Shared,)",
            "assert Target.__bases__ == Child.__bases__ == (models.Model,)",
            "assert Link._meta.get_field('target').remote_field.model == 'typing_composition.Target'",
            "assert apps.get_model('typing_composition', 'Link')._meta.get_field('target').related_model is emitted",
            "assert context.get_model_class_by_fullname('typing_composition.models.Target') is emitted",
            "assert context.get_model_fullname(emitted) == 'typing_composition.models.Target'",
            "assert context.get_model_fullname(Ordinary) == 'typing_composition.models.Ordinary'",
            "assert before == ProjectState.from_apps(apps)",
        ])], cwd=root, env=env, capture_output=True, text=True, check=False,
    )
    assert native.returncode == 0, native.stdout + native.stderr
