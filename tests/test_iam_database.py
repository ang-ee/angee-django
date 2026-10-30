"""Initial-admin bootstrap delegates all selected storage to native owners."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_bootstrap_admin_selected_database_and_explicit_provision_intent():
    script = r'''
import os
from io import StringIO
from types import SimpleNamespace
for name in tuple(os.environ):
    if name.startswith(('ANGEE_BOOTSTRAP_ADMIN_', 'DJANGO_SUPERUSER_')):
        os.environ.pop(name)
from django.conf import settings
from angee.iam.apps import IAMConfig
class TestIAMConfig(IAMConfig):
    def import_models(self):
        super().import_models()
        from angee.iam.models import User as AbstractUser
        class User(AbstractUser):
            class Meta(AbstractUser.Meta):
                abstract = False
                app_label = 'iam'
class DefaultRouter:
    def db_for_read(self, model, **hints):
        return 'default'
    db_for_write = db_for_read
settings.configure(
    INSTALLED_APPS=['django.contrib.contenttypes', 'django.contrib.auth', 'rebac', 'angee.base',
                    '__main__.TestIAMConfig'],
    AUTH_USER_MODEL='iam.User',
    SECRET_KEY='iam-selected', ANGEE_MERGE_GUARDS=[],
    REBAC_LOCAL_BACKEND_STORAGE='registry', REBAC_UNIVERSAL_ADMIN_ROLE='angee/role:admin',
    DATABASE_ROUTERS=[DefaultRouter()],
    DATABASES={alias: {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}
               for alias in ('default', 'selected')},
)
import django
django.setup()
from django.core.management import call_command
from django.contrib.auth import get_user_model
from rebac import backend, database_context, system_context
from rebac.models import PermissionAuditEvent, SchemaDefinition, SchemaRelation, active_relationship_model
from rebac.models.schema_write import schema_changes
from angee.iam.apps import IAMConfig
from angee.iam.management.commands.bootstrap_admin import Command
for alias in ('default', 'selected'):
    call_command('migrate', database=alias, run_syncdb=True, verbosity=0)
with schema_changes(using='selected'):
    SchemaDefinition.objects.using('selected').create(resource_type='auth/user')
    definition = SchemaDefinition.objects.using('selected').create(resource_type='angee/role')
    SchemaRelation.objects.using('selected').create(
        definition_id=definition.pk, name='member', allowed_subjects=[{'type': 'auth/user'}])
User = get_user_model()
User.objects.db_manager('default').create_user(username='admin', email='default@example.test')
command = Command(stdout=StringIO())
with database_context('default'), system_context(reason='test.iam.bootstrap'):
    # Fixture creation and this test's system context legitimately audit on default.
    default_audit = list(PermissionAuditEvent.objects.using('default').order_by('pk').values())
    user, created = User.objects.db_manager('selected').ensure_bootstrap_admin(
        'admin', email='selected@example.test', password='initial-secret')
    assert created
    assert user._state.db == 'selected'
    first_hash = user.password
    command.handle(database='selected', username='admin', email='selected@example.test', password='ignored-secret')
    user.refresh_from_db(using='selected')
    assert user.password == first_hash and user.check_password('initial-secret')
    assert backend().schema().get_definition('angee/role') is None
    assert list(PermissionAuditEvent.objects.using('default').order_by('pk').values()) == default_audit
assert user.is_staff and user.is_superuser and user.is_active
assert user.email == 'selected@example.test'
with system_context(reason='test.iam.bootstrap'):
    assert User.objects.using('default').get(username='admin').email == 'default@example.test'
assert 'initial-secret' not in command.stdout.getvalue()
assert 'ignored-secret' not in command.stdout.getvalue()
assert active_relationship_model().objects.using('selected').filter(
    resource_type='angee/role', resource_id='admin', relation='member').count() == 1
assert not active_relationship_model().objects.using('default').exists()
assert PermissionAuditEvent.objects.using('selected').filter(
    kind=PermissionAuditEvent.KIND_RELATIONSHIP_GRANT).exists()
import angee.iam
config = IAMConfig('angee.iam', angee.iam)
calls = []
context = SimpleNamespace(database='selected', call=lambda *args, **kwargs: calls.append((args, kwargs)))
config.compose_provision(context)
assert calls == []
settings.ANGEE_BOOTSTRAP_ADMIN_PASSWORD = 'explicit-secret'
config.compose_provision(context)
assert calls == [(('bootstrap_admin',), {'database': 'selected'})]
'''
    result = subprocess.run(
        [sys.executable, '-c', script], cwd=Path(__file__).resolve().parents[1],
        env=dict(os.environ), capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
