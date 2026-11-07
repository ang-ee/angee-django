"""Counter allocation stays with the native Sequence row."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_native_sequence_counter_uses_bound_manager_and_row_database():
    script = r'''
from django.conf import settings
settings.configure(
    INSTALLED_APPS=['django.contrib.contenttypes', 'django.contrib.auth', 'rebac',
                    'angee.base', 'angee.sequence'],
    SECRET_KEY='sequence-test', ANGEE_MERGE_GUARDS=[],
    REBAC_LOCAL_BACKEND_STORAGE='registry',
    DATABASES={'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}},
)
import django
django.setup()
from angee.sequence.testing.models import Sequence, SequenceCounter
from django.core.management import call_command
from django.db import transaction
from django.utils import timezone
from rebac import system_context
call_command('migrate', run_syncdb=True, verbosity=0)
with system_context(reason='tests.sequence.fixture'):
    sequence = Sequence(key='shared-key', name='Sequence', template='{prefix}{number}', prefix='S',
                        period_reset='none', preview_enabled=True)
    sequence.save()
    SequenceCounter.objects.create(
        sequence_id=sequence.pk, period=sequence.period_key(timezone.localdate()), value=9)
with system_context(reason='tests.sequence.draw'):
    manager = Sequence.objects
    assert manager.preview_next('shared-key') == 'S10'
    with transaction.atomic():
        assert manager.next_value('shared-key') == 'S10'
    assert manager.preview_next('shared-key') == 'S11'
    sequence = manager.get(key='shared-key')
    with transaction.atomic():
        assert SequenceCounter.objects.draw(sequence, sequence.period_key(timezone.localdate())) == 11
    assert SequenceCounter.objects.peek(sequence, sequence.period_key(timezone.localdate())) == 11
with system_context(reason='tests.sequence.verify'):
    assert SequenceCounter.objects.get().value == 11
'''
    result = subprocess.run(
        [sys.executable, '-c', script], cwd=Path(__file__).resolve().parents[1],
        env=dict(os.environ), capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
