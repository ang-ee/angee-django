"""Explicit seed attribution shares its operation's native database alias."""

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("outcome", ["success", "failure"])
def test_explicit_changeset_database_preserves_default_and_restores_scope(outcome):
    root = Path(__file__).resolve().parents[1]
    script = f"expected_outcome = {outcome!r}\n" + """
from django.conf import settings
settings.configure(INSTALLED_APPS=[], SECRET_KEY='alias-test',
    ANGEE_CHANGESET_MODEL='alias_test.Changeset',
    DATABASES={alias: {'ENGINE':'django.db.backends.sqlite3','NAME':':memory:'}
               for alias in ('default','selected')})
import django
django.setup()
from django.db import models, connections, transaction
class DefaultRouter:
    def db_for_write(self, model, **hints):
        return "default"
settings.DATABASE_ROUTERS = [DefaultRouter()]
from angee.provenance import context
class Changeset(models.Model):
    actor_ref = models.CharField(max_length=100)
    change_reason = models.CharField(max_length=100, default='')
    outcome = models.CharField(max_length=20, default='open')
    occurred_at = models.DateTimeField()
    class Meta:
        app_label = 'alias_test'
    @classmethod
    def ambient_attribution(cls):
        return '', 'alias-test'
    def close(self, outcome):
        assert connections[self._state.db].in_atomic_block
        self.outcome = outcome
        self.save(using=self._state.db, update_fields=['outcome'])
context.changeset_model = lambda: Changeset
for alias in connections:
    with connections[alias].schema_editor() as editor:
        editor.create_model(Changeset)
with context.changeset(fresh=True, using='default'):
    outer = context.current_changeset()
    with transaction.atomic(using='selected'):
        try:
            with context.changeset(fresh=True, using='selected'):
                row = context.current_changeset(record=True)
                assert row._state.db == 'selected'
                if expected_outcome == 'failure':
                    raise ValueError('record failure')
        except ValueError:
            pass
        assert row.outcome == 'open'
    assert context.current_changeset() is outer
assert context.current_changeset() is None
assert Changeset.objects.using('default').count() == 0
assert Changeset.objects.using('selected').get().outcome == expected_outcome
"""
    environment = {key: value for key, value in os.environ.items() if key != 'DJANGO_SETTINGS_MODULE'}
    environment['PYTHONPATH'] = os.pathsep.join((str(root / 'core'), str(root / 'addons'), str(root)))
    result = subprocess.run([sys.executable, '-c', script], cwd=root, env=environment,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
