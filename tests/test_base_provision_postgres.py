"""Opt-in native PostgreSQL proof for provision's database owners.

Supply ANGEE_PROVISION_POSTGRES_ENV as a private JSON file containing
REBAC_TEST_POSTGRES_URL. Only its connection credentials are used: the database
name is replaced before native Django creates a unique test database. No compiler
lease is substituted or claimed here.
"""
from __future__ import annotations

import json
import multiprocessing
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _configure(database: dict[str, object], *, pooled: bool) -> None:
    import django
    from django.conf import settings

    options = dict(database['OPTIONS'])
    if pooled:
        options['pool'] = {'min_size': 0, 'max_size': 1, 'timeout': 2}
    config = {**database, 'OPTIONS': options}
    settings.configure(
        INSTALLED_APPS=[], SECRET_KEY='provision-owner-proof', USE_TZ=True,
        DATABASES={'default': config, 'observer': {**database, 'OPTIONS': dict(database['OPTIONS'])}},
    )
    django.setup()


def _runner(database, digest, *, first, entered, release, invalidated, complete, attempting, outcomes):
    _configure(database, pooled=True)
    from django.db import connections

    from angee.base.provision import PROVISION_LOCK, ProvisionRecorder, advisory_lock

    connection = connections['default']
    try:
        # Occupy the single pool slot throughout. Lock acquisition and recorder
        # writes must still succeed: the advisory session cannot borrow this slot.
        connection.ensure_connection()
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_backend_pid()')
            pooled_pid = cursor.fetchone()[0]
        attempting.set()
        try:
            with advisory_lock(connection):
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT pid FROM pg_locks WHERE locktype='advisory' AND granted "
                        'AND database=(SELECT oid FROM pg_database WHERE datname=current_database()) '
                        'AND classid=%s AND objid=%s AND objsubid=1',
                        (PROVISION_LOCK >> 32, PROVISION_LOCK & 0xFFFFFFFF),
                    )
                    lock_pid = cursor.fetchone()[0]
                assert lock_pid != pooled_pid, 'advisory session borrowed the occupied pool slot'
                outcomes.put(('physical', digest, lock_pid))
                entered.set()
                if not first:
                    assert release.wait(15), 'second runner was not released'
                recorder = ProvisionRecorder(connection)
                recorder.ensure_schema()
                recorder.write('in_progress', digest, 'compiler:proof')
                invalidated.set()
                if first:
                    assert release.wait(15), 'first runner was not released'
                    raise RuntimeError('simulated failing provision hook')
                assert complete.wait(15), 'completion was not released'
                recorder.write('complete', digest, 'compiler:proof')
                outcomes.put(('complete', digest))
        except RuntimeError as error:
            if not first or str(error) != 'simulated failing provision hook':
                raise
            outcomes.put(('hook_failed', digest))
    finally:
        connection.close()
        connection.close_pool()
        connections.close_all()


def _drive(private_environment: Path) -> None:
    environment = json.loads(private_environment.read_text())
    url = urlsplit(environment['REBAC_TEST_POSTGRES_URL'])
    assert url.scheme in {'postgres', 'postgresql'}
    # The supplied base database name is deliberately never put in DATABASES.
    unique_base = f'angee_baseprov_{os.getpid()}_{uuid4().hex[:10]}'
    database = {
        'ENGINE': 'django.db.backends.postgresql', 'NAME': unique_base,
        'USER': unquote(url.username or ''), 'PASSWORD': unquote(url.password or ''),
        'HOST': url.hostname or '', 'PORT': str(url.port or 5432),
        'OPTIONS': dict(parse_qsl(url.query)),
    }
    _configure(database, pooled=False)
    from django.db import connections

    from angee.base.provision import PROVISION_LOCK, ProvisionRecorder, advisory_lock

    connection = connections['default']
    old_name = connection.settings_dict['NAME']
    test_name = None
    workers = []
    context = multiprocessing.get_context('spawn')
    outcomes = context.Queue()
    try:
        test_name = connection.creation.create_test_db(verbosity=0, autoclobber=False)
        assert test_name == f'test_{unique_base}'
        selected = {**database, 'NAME': test_name}
        connections['observer'].settings_dict['NAME'] = test_name
        recorder = ProvisionRecorder(connection)
        observer = ProvisionRecorder(connections['observer'])
        with advisory_lock(connection):
            recorder.ensure_schema()
            recorder.write('complete', 'previous', 'compiler:proof')
        first_entered, first_release, first_invalidated = (context.Event() for _ in range(3))
        second_entered, second_release, second_invalidated, second_complete = (context.Event() for _ in range(4))
        first_attempting, second_attempting = context.Event(), context.Event()
        first_complete = context.Event()  # Retain every semaphore until spawned children finish loading it.
        for digest, first, entered, release, invalidated, complete, attempting in (
            ('receipt-a', True, first_entered, first_release, first_invalidated, first_complete, first_attempting),
            (
                'receipt-b', False, second_entered, second_release,
                second_invalidated, second_complete, second_attempting,
            ),
        ):
            process = context.Process(target=_runner, kwargs={
                'database': selected, 'digest': digest, 'first': first, 'entered': entered,
                'release': release, 'invalidated': invalidated, 'complete': complete,
                'attempting': attempting, 'outcomes': outcomes,
            })
            workers.append(process)
        workers[0].start()
        assert first_invalidated.wait(15), 'first invalidation did not finish with pool size one'
        assert observer.read().state == 'in_progress'
        assert observer.read().digest == 'receipt-a'
        workers[1].start()
        assert second_attempting.wait(15)
        # Observe a real PostgreSQL waiter, rather than inferring serialization
        # from thread scheduling or an arbitrary sleep.
        deadline = time.monotonic() + 10
        waiting = False
        while time.monotonic() < deadline:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' AND NOT granted "
                    'AND database=(SELECT oid FROM pg_database WHERE datname=current_database()) '
                    'AND classid=%s AND objid=%s AND objsubid=1)',
                    (PROVISION_LOCK >> 32, PROVISION_LOCK & 0xFFFFFFFF),
                )
                waiting = cursor.fetchone()[0]
            if waiting:
                break
            time.sleep(0.02)
        assert waiting and not second_entered.is_set(), 'different receipt digests did not serialize'
        first_release.set()
        workers[0].join(10)
        assert workers[0].exitcode == 0
        assert second_entered.wait(10), 'failure did not release the advisory session'
        assert observer.read().state == 'in_progress' and observer.read().digest == 'receipt-a'
        second_release.set()
        assert second_invalidated.wait(10)
        assert observer.read().state == 'in_progress' and observer.read().digest == 'receipt-b'
        second_complete.set()
        workers[1].join(10)
        assert workers[1].exitcode == 0
        assert observer.read().state == 'complete' and observer.read().digest == 'receipt-b'
        facts = [outcomes.get(timeout=2) for _ in range(4)]
        assert ('hook_failed', 'receipt-a') in facts and ('complete', 'receipt-b') in facts
        physical_pids = [fact[2] for fact in facts if fact[0] == 'physical']
        with connection.cursor() as cursor:
            cursor.execute('SELECT count(*) FROM pg_stat_activity WHERE pid = ANY(%s)', (physical_pids,))
            assert cursor.fetchone()[0] == 0, 'physical advisory sessions survived runner exit'
        # A retry of the same receipt must invalidate durably too.
        with advisory_lock(connection):
            recorder.write('in_progress', 'receipt-b', 'compiler:proof')
            assert observer.read().state == 'in_progress'
            recorder.write('complete', 'receipt-b', 'compiler:proof')
        assert observer.read().state == 'complete' and recorder.records.count() == 1
    finally:
        for worker in workers:
            if worker.pid is not None:
                if worker.is_alive():
                    worker.terminate()
                worker.join(5)
        connections.close_all()
        if test_name is not None:
            connection.creation.destroy_test_db(old_database_name=old_name, verbosity=0)
            with connection._nodb_cursor() as cursor:
                cursor.execute('SELECT count(*) FROM pg_database WHERE datname=%s', (test_name,))
                assert cursor.fetchone()[0] == 0, 'native test database teardown failed'
        outcomes.close()
        outcomes.join_thread()
    print('PostgreSQL provision owners: serialization, pool isolation, durable retry and teardown verified')


def test_postgres_provision_owners_serialize_with_one_pool_slot(tmp_path: Path) -> None:
    environment = os.environ.get('ANGEE_PROVISION_POSTGRES_ENV')
    if not environment:
        pytest.skip('requires an explicit private PostgreSQL test environment')
    result = subprocess.run(
        [sys.executable, '-B', str(Path(__file__).resolve()), '--probe', environment],
        env={**os.environ, 'PYTHONPATH': str(ROOT / 'addons')},
        capture_output=True, text=True, timeout=60, check=False,
    )
    if result.returncode:
        # Connection failures must never print the private environment/credentials.
        diagnostic = tmp_path / 'provision-postgres-private.stderr'
        descriptor = os.open(diagnostic, os.O_CREAT | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, 'w') as stream:
            stream.write(result.stderr)
        pytest.fail(f'PostgreSQL provision owner proof failed; private diagnostic: {diagnostic}')
    assert 'serialization, pool isolation, durable retry and teardown verified' in result.stdout


if __name__ == '__main__':
    assert sys.argv[1] == '--probe'
    _drive(Path(sys.argv[2]))
