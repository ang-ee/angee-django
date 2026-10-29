"""Angee's durable provision lifecycle, independent of compiler generation."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _probe(source: str, *args: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, "-c", source, *args],
        env={**os.environ, "PYTHONPATH": str(ROOT / "addons")},
        text=True, capture_output=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stderr
    return result


_SETUP = '''
import sys
import django
from django.conf import settings
settings.configure(INSTALLED_APPS=[], SECRET_KEY='test', DATABASES={
    'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': sys.argv[1]},
    'other': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': sys.argv[2]},
})
django.setup()
from django.apps import apps
from django.db import connections
from angee.base.provision import ProvisionRecorder
'''


def test_provision_record_is_private_and_all_queries_use_selected_alias(tmp_path: Path) -> None:
    result = _probe(_SETUP + '''
from django.db.migrations.state import ProjectState
recorder = ProvisionRecorder(connections['other'])
recorder.ensure_schema()
recorder.ensure_schema()
recorder.write('in_progress', 'a' * 64, 'compiler:1')
assert recorder.records.db == 'other'
assert recorder.read().state == 'in_progress'
assert not ProvisionRecorder(connections['default']).has_table()
assert recorder.Record._meta.apps is not apps
assert not any(label == 'base_provision' for label, _name in ProjectState.from_apps(apps).models)
recorder.write('complete', 'a' * 64, 'compiler:1')
assert recorder.records.count() == 1
assert recorder.read().compiler == 'compiler:1'
print('private alias record verified')
''', str(tmp_path / 'default.sqlite3'), str(tmp_path / 'other.sqlite3'))
    assert result.stdout.strip() == 'private alias record verified'


def test_crash_and_same_or_different_digest_retry_leave_durable_invalidation(tmp_path: Path) -> None:
    default = str(tmp_path / 'default.sqlite3')
    other = str(tmp_path / 'other.sqlite3')
    _probe(_SETUP + '''
import os
recorder = ProvisionRecorder(connections['other'])
recorder.ensure_schema()
recorder.write('complete', 'old', 'compiler:1')
recorder.write('in_progress', 'same', 'compiler:1')
os._exit(0)
''', default, other)
    result = _probe(_SETUP + '''
recorder = ProvisionRecorder(connections['other'])
assert recorder.read().state == 'in_progress'
assert recorder.read().digest == 'same'
for digest in ('same', 'new'):
    recorder.write('in_progress', digest, 'compiler:2')
    assert recorder.read().state == 'in_progress'
    recorder.write('complete', digest, 'compiler:2')
    assert recorder.read().state == 'complete'
    assert recorder.read().digest == digest
print('durable retry verified')
''', default, other)
    assert result.stdout.strip() == 'durable retry verified'


def test_provision_record_refuses_outer_transaction(tmp_path: Path) -> None:
    result = _probe(_SETUP + '''
from django.db import transaction
from django.core.management.base import CommandError
recorder = ProvisionRecorder(connections['other'])
recorder.ensure_schema()
with transaction.atomic(using='other'):
    try:
        recorder.write('in_progress', 'a', 'compiler:1')
    except CommandError:
        pass
    else:
        raise AssertionError('record invalidation would be rolled back')
assert recorder.read() is None
print('transaction refusal verified')
''', str(tmp_path / 'default.sqlite3'), str(tmp_path / 'other.sqlite3'))
    assert result.stdout.strip() == 'transaction refusal verified'


def test_advisory_lock_is_unpooled_constant_and_physically_closed_on_hook_failure() -> None:
    result = _probe('''
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from angee.base.provision import advisory_lock, PROVISION_LOCK
physical = MagicMock()
cursor = physical.cursor.return_value.__enter__.return_value
connection = SimpleNamespace(vendor='postgresql', get_connection_params=lambda: {'dbname':'test','pool':{'max_size':1}})
with patch('psycopg.connect', return_value=physical) as connect:
    try:
        with advisory_lock(connection):
            raise RuntimeError('hook failed')
    except RuntimeError:
        pass
connect.assert_called_once_with(dbname='test', autocommit=True)
assert cursor.execute.call_args_list[0].args == ('SELECT pg_advisory_lock(%s)', (PROVISION_LOCK,))
assert cursor.execute.call_args_list[1].args == ('SELECT pg_advisory_unlock(%s)', (PROVISION_LOCK,))
physical.close.assert_called_once()
print('unpooled failure cleanup verified')
''')
    assert result.stdout.strip() == 'unpooled failure cleanup verified'


def test_hook_failure_keeps_in_progress_and_retry_completes_last(tmp_path: Path) -> None:
    result = _probe(_SETUP + '''
from contextlib import contextmanager
from io import StringIO
from types import ModuleType, SimpleNamespace
from unittest.mock import patch
from django.apps import AppConfig
from django.core.management.base import BaseCommand, CommandError
from angee.base.provision import run
import angee.base.provision as provision
# The compiler lease is an explicit integration dependency. This tests base's
# ordering/durability around that boundary, not the external compiler lock.
events=[]
@contextmanager
def lease():
    events.append('lease-enter')
    try:
        yield SimpleNamespace(DIGEST='verified', COMPILER='compiler:1')
    finally:
        events.append('lease-exit')
@contextmanager
def lock(connection):
    events.append('lock-enter')
    try:
        yield
    finally:
        events.append('lock-exit')
native=ModuleType('django_compose.serving')
native.receipt_lease=lease
sys.modules['django_compose.serving']=native
module=ModuleType('native_hook')
module.__file__=sys.argv[1]
sys.modules[module.__name__]=module
apps.set_installed_apps(['native_hook'])
settings.MIGRATION_MODULES={'native_hook':None}
config=apps.get_app_config('native_hook')
recorder=ProvisionRecorder(connections['other'])
fail=[True]
def hook(context):
    assert context.database=='other'
    assert recorder.read().state=='in_progress'
    events.append('hook')
    if fail[0]:
        raise ValueError('hook failed')
config.compose_provision=hook
command=BaseCommand(stdout=StringIO(),stderr=StringIO())
options={'database':'other','check':False,'wait_db':0,'verbosity':0}
def step(command,args):
    assert recorder.read().state=='in_progress'
    events.append(args[0])
with (
    patch.object(provision,'advisory_lock',lock),
    patch.object(provision,'run_step',step),
    patch.object(provision,'migrate_and_analyze',lambda *args,**kwargs:events.append('migrate')),
):
    try:
        run(command,options)
    except CommandError as error:
        assert 'native_hook.compose_provision failed' in str(error)
    else:
        raise AssertionError('hook failure was ignored')
    assert recorder.read().state=='in_progress'
    assert events[:2]==['lease-enter','lock-enter']
    assert events[-2:]==['lock-exit','lease-exit']
    fail[0]=False
    events.clear()
    run(command,options)
    assert events==[
        'lease-enter','lock-enter','check_migrations','check_migrations','migrate','hook','check','lock-exit','lease-exit',
    ]
    assert recorder.read().state=='complete'
    assert recorder.read().digest=='verified'
print('failure retry lifecycle verified')
''', str(tmp_path / 'default.sqlite3'), str(tmp_path / 'other.sqlite3'))
    assert result.stdout.strip() == 'failure retry lifecycle verified'


def test_readiness_reports_missing_in_progress_and_receipt_mismatch(tmp_path: Path) -> None:
    result = _probe(_SETUP + '''
from contextlib import contextmanager
from types import ModuleType, SimpleNamespace
from django.core.management.base import CommandError
from angee.base.provision import require_provisioned
@contextmanager
def lease():
    yield SimpleNamespace(DIGEST='verified',COMPILER='compiler:1')
native=ModuleType('django_compose.serving')
native.receipt_lease=lease
sys.modules['django_compose.serving']=native
recorder=ProvisionRecorder(connections['other'])
def refuses(expected):
    try:
        require_provisioned(using='other')
    except CommandError as error:
        assert expected in str(error),str(error)
    else:
        raise AssertionError('unready database accepted')
refuses('missing provision record')
assert not recorder.has_table()
recorder.ensure_schema()
recorder.write('in_progress','verified','compiler:1')
refuses('in_progress')
recorder.write('complete','stale','compiler:1')
refuses('digest differs')
recorder.write('complete','verified','compiler:0')
refuses('compiler version differs')
recorder.write('complete','verified','compiler:1')
require_provisioned(using='other')
assert not ProvisionRecorder(connections['default']).has_table()
print('readiness state verified')
''', str(tmp_path / 'default.sqlite3'), str(tmp_path / 'other.sqlite3'))
    assert result.stdout.strip() == 'readiness state verified'
