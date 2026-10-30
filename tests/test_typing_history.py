"""Native history symbols and descriptors type without mutating compiler output."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_native_history_projection_preserves_runtime_and_type_checks(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    runtime = tmp_path / "runtime"
    settings = tmp_path / "history_settings.py"
    settings.write_text(
        "from pathlib import Path\n"
        "from django_compose.conf import configure\n"
        "SECRET_KEY = 'native-history-typing'\n"
        "INSTALLED_APPS = ['django.contrib.contenttypes', 'django.contrib.auth', 'rebac', "
        "'tests.typing_history.apps.TypingHistoryConfig']\n"
        "DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}\n"
        "DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'\n"
        f"COMPOSE_OUTPUT_DIR = Path({str(runtime)!r})\n"
        "COMPOSE_OUTPUT_MODULE = 'runtime'\n"
        "ANGEE_CHANGESET_MODEL = 'typing_history.Changeset'\n"
        "configure(globals())\n"
    )
    env = dict(os.environ)
    env.pop("DJANGO_COMPOSE_MODE", None)
    env["PYTHONPATH"] = os.pathsep.join(
        filter(
            None,
            (
                str(tmp_path),
                str(root),
                str(root / "src"),
                env.get("PYTHONPATH"),
            ),
        )
    )
    env["DJANGO_SETTINGS_MODULE"] = "history_settings"
    native = subprocess.run(
        [sys.executable, "-c", "from django_compose.management import execute; execute(['manage.py','compose'])"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert native.returncode == 0, native.stdout + native.stderr
    before = {path.relative_to(runtime): path.read_bytes() for path in runtime.rglob("*.py")}
    assert b"class HistoricalSubject" not in before[Path("typing_history/models.py")]
    assert b"class SubjectStatement" not in before[Path("typing_history/models.py")]

    config = tmp_path / "mypy.ini"
    config.write_text(
        "[mypy]\nplugins = angee.base.typing,angee.provenance.typing\n"
        f"mypy_path = {tmp_path},{root},{root / 'src'}\n"
        "ignore_missing_imports = True\ncache_dir = /dev/null\n"
        "[mypy-simple_history.*,django_sqids.*]\nfollow_untyped_imports = True\n"
        "[mypy.plugins.django-stubs]\ndjango_settings_module = history_settings\n"
    )
    valid = tmp_path / "valid.py"
    valid.write_text(
        "from typing import assert_type\n"
        "from angee.provenance.statements import Statement, StatementPair\n"
        "from tests.typing_history.models import FixtureHistoricalQuerySet\n"
        "from runtime.typing_history.models import "
        "Subject, SubjectStatement, HistoricalSubject, HistoricalSubjectStatement\n"
        "assert_type(SubjectStatement.objects.get().title, str | None)\n"
        "assert_type(SubjectStatement.objects.get().rank, Statement.StatementRank)\n"
        "assert_type(SubjectStatement.objects.get().count, int | None)\n"
        "assert_type(SubjectStatement.objects.get().subject, Subject)\n"
        "assert_type(Subject().subjectstatement_set.get().count, int | None)\n"
        "assert_type(HistoricalSubject.objects.get().count, int)\n"
        "assert_type(HistoricalSubjectStatement.objects.get().subject, Subject | None)\n"
        "assert_type(Subject.history.get(), HistoricalSubject)\n"
        "assert_type(Subject().history.get().count, int)\n"
        "assert_type(Subject.statements.get(), SubjectStatement)\n"
        "assert_type(Subject().statements.get().subject, Subject)\n"
        "assert_type(SubjectStatement.history.get(), HistoricalSubjectStatement)\n"
        "assert_type(Subject.history.filter(count=1), FixtureHistoricalQuerySet[HistoricalSubject])\n"
        "assert_type(Subject.history.filter().latest_of_each().tagged().get().count, int)\n"
        "assert_type(Subject.history.latest_of_each().get(), HistoricalSubject)\n"
        "assert_type(Subject.history.label(), str)\n"
        "assert_type(Subject.history.filter().label(), int)\n"
        "SubjectStatement.history.filter().latest_of_each()\n"
        "from simple_history.models import HistoricalChanges\n"
        "from tests.typing_history.models import Subject as AbstractSubject\n"
        "assert_type(AbstractSubject.history.get(), HistoricalChanges)\n"
        "def abstract_history(record: AbstractSubject) -> None:\n"
        "    assert_type(record.history.filter().tagged(), FixtureHistoricalQuerySet[HistoricalChanges])\n"
        "assert_type(AbstractSubject.statements.get(), Statement)\n"
        "assert_type(AbstractSubject.same_as.get(), StatementPair)\n"
        "def generic_history[T: AbstractSubject](record: T, model: type[T]) -> None:\n"
        "    assert_type(record.history.filter().tagged().get(), HistoricalChanges)\n"
        "    assert_type(model.history.filter().latest_of_each(), FixtureHistoricalQuerySet[HistoricalChanges])\n"
        "    assert_type(record.statements.get(), Statement)\n"
        "    assert_type(model.same_as.filter().get(), StatementPair)\n"
        "from django.db.models import Prefetch\n"
        "assert_type(AbstractSubject.same_as.model._base_manager.filter(left_id=1, right_id=2).get(), StatementPair)\n"
        "AbstractSubject.same_as.model._base_manager.prefetch_related("
        "Prefetch('left', queryset=AbstractSubject.objects.all()))\n"
        "def union_history(record: AbstractSubject | SubjectStatement) -> None:\n"
        "    assert_type(record.history.filter().get(), HistoricalChanges)\n"
        "def union_statements(record: AbstractSubject | Subject) -> None:\n"
        "    assert_type(record.statements.get(), Statement)\n"
    )
    invalid = tmp_path / "invalid.py"
    invalid.write_text(
        "from runtime.typing_history.models import Subject, SubjectStatement, Changeset\n"
        "SubjectStatement.objects.get().count = {}\n"
        "SubjectStatement.objects.get().subject = Changeset()\n"
        "Subject().subjectstatement_set.missing_method()\n"
        "Subject.history.get().count = {}\n"
        "Subject().statements.get().count = {}\n"
        "wrong_queryset_label: str = Subject.history.filter().label()\n"
        "wrong_manager_label: int = Subject.history.label()\n"
        "Subject.history.filter().missing_queryset_method()\n"
        "from tests.typing_history.models import Subject as AbstractSubject\n"
        "AbstractSubject.history.filter().missing_queryset_method()\n"
        "wrong_abstract_label: str = AbstractSubject.history.filter().label()\n"
        "def invalid_generic[T: AbstractSubject](record: T) -> None:\n"
        "    record.history.missing_method()\n"
        "    record.statements.missing_method()\n"
        "def invalid_union(record: AbstractSubject | SubjectStatement) -> None:\n"
        "    wrong_union_row: int = record.history.get()\n"
        "AbstractSubject.same_as.model._base_manager.filter(left_id__in=1)\n"
        "AbstractSubject.same_as.model._base_manager.filter(right_id__isnull='bad')\n"
        "AbstractSubject.same_as.model._base_manager.filter(missing_pair_field=1)\n"
    )
    # The native plugin owns TypeInfos only. Compare actual Django model/state
    # identities after semantic analysis to the fixture's pre-analysis snapshot.
    script = "\n".join(
        [
            "import sys",
            "from mypy import api",
            "result=api.run(sys.argv[1:])",
            "from django.apps import apps",
            "from django.db.migrations.state import ProjectState",
            "from angee.provenance.statements import StatementPair",
            "assert not {'left', 'right'}.intersection(field.name for field in StatementPair._meta.get_fields())",
            "config=apps.get_app_config('typing_history')",
            "assert config.before_typing_state==ProjectState.from_apps(apps)",
            "assert config.before_typing_models==dict((model.__name__,model) for model in config.get_models())",
            "sys.stdout.write(result[0]);sys.stderr.write(result[1]);sys.exit(result[2])",
        ]
    )
    command = [sys.executable, "-c", script, "--config-file", str(config)]
    positive = subprocess.run(
        [*command, str(valid), str(runtime / "typing_history" / "models.py")],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert positive.returncode == 0, positive.stdout + positive.stderr
    negative = subprocess.run([*command, str(invalid)], cwd=root, env=env, capture_output=True, text=True, check=False)
    assert negative.returncode == 1, negative.stdout + negative.stderr
    assert negative.stdout.count(": error:") == 16, negative.stdout
    for line in (2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 14, 15, 17, 18, 19, 20):
        assert f"invalid.py:{line}: error:" in negative.stdout, negative.stdout
    assert {path.relative_to(runtime): path.read_bytes() for path in runtime.rglob("*.py")} == before
    assert not tuple(runtime.rglob("*.pyi"))
