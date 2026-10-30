"""Counter allocation stays with the selected native Sequence row."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_native_sequence_counter_uses_bound_manager_and_row_database():
    script = r'''
from django.conf import settings
class DefaultRouter:
    def db_for_read(self, model, **hints):
        return 'default'
    db_for_write = db_for_read
settings.configure(
    INSTALLED_APPS=['django.contrib.contenttypes', 'django.contrib.auth', 'rebac',
                    'angee.base', 'angee.sequence'],
    SECRET_KEY='selected-sequence', ANGEE_MERGE_GUARDS=[], DATABASE_ROUTERS=[DefaultRouter()],
    REBAC_LOCAL_BACKEND_STORAGE='registry',
    DATABASES={alias: {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}
               for alias in ('default', 'selected')},
)
import django
django.setup()
from angee.sequence.testing.models import Sequence, SequenceCounter
from django.core.management import call_command
from django.db import transaction
from django.utils import timezone
from rebac import database_context, system_context
for alias, prefix in (('default', 'D'), ('selected', 'S')):
    call_command('migrate', database=alias, run_syncdb=True, verbosity=0)
    with system_context(reason='tests.sequence.fixture'):
        sequence = Sequence(key='shared-key', name=prefix, template='{prefix}{number}', prefix=prefix,
                            period_reset='none', preview_enabled=True)
        sequence.save(using=alias)
        SequenceCounter.objects.db_manager(alias).create(
            sequence_id=sequence.pk, period=sequence.period_key(timezone.localdate()), value=9)
with database_context('default'), system_context(reason='tests.sequence.draw'):
    manager = Sequence.objects.db_manager('selected')
    assert manager.preview_next('shared-key') == 'S10'
    with transaction.atomic(using='selected'):
        assert manager.next_value('shared-key') == 'S10'
    assert manager.preview_next('shared-key') == 'S11'
    selected = manager.get(key='shared-key')
    with transaction.atomic(using='selected'):
        assert SequenceCounter.objects.draw(selected, selected.period_key(timezone.localdate())) == 11
    assert SequenceCounter.objects.peek(selected, selected.period_key(timezone.localdate())) == 11
with system_context(reason='tests.sequence.verify'):
    assert SequenceCounter.objects.using('selected').get().value == 11
    assert SequenceCounter.objects.using('default').get().value == 9
'''
    result = subprocess.run(
        [sys.executable, '-c', script], cwd=Path(__file__).resolve().parents[1],
        env=dict(os.environ), capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
