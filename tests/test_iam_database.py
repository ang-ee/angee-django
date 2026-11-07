"""Initial-admin bootstrap delegates storage to native owners."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_bootstrap_admin_and_explicit_provision_intent():
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
settings.configure(
    INSTALLED_APPS=['django.contrib.contenttypes', 'django.contrib.auth', 'rebac', 'angee.base',
                    '__main__.TestIAMConfig'],
    AUTH_USER_MODEL='iam.User',
    SECRET_KEY='iam-selected', ANGEE_MERGE_GUARDS=[],
    REBAC_LOCAL_BACKEND_STORAGE='registry', REBAC_UNIVERSAL_ADMIN_ROLE='angee/role:admin',
    DATABASES={'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}},
)
import django
django.setup()
from django.core.management import call_command
from django.contrib.auth import get_user_model
from rebac import backend, system_context
from rebac.models import PermissionAuditEvent, SchemaDefinition, SchemaRelation, active_relationship_model
from rebac.models.schema_write import schema_changes
from angee.iam.apps import IAMConfig
from angee.iam.management.commands.bootstrap_admin import Command
call_command('migrate', run_syncdb=True, verbosity=0)
with schema_changes():
    SchemaDefinition.objects.create(resource_type='auth/user')
    definition = SchemaDefinition.objects.create(resource_type='angee/role')
    SchemaRelation.objects.create(
        definition_id=definition.pk, name='member', allowed_subjects=[{'type': 'auth/user'}])
User = get_user_model()
command = Command(stdout=StringIO())
with system_context(reason='test.iam.bootstrap'):
    user, created = User.objects.ensure_bootstrap_admin(
        'admin', email='admin@example.test', password='initial-secret')
    assert created
    assert user._state.db == 'default'
    first_hash = user.password
    command.handle(username='admin', email='admin@example.test', password='ignored-secret')
    user.refresh_from_db()
    assert user.password == first_hash and user.check_password('initial-secret')
    assert backend().schema().get_definition('angee/role') is not None
assert user.is_staff and user.is_superuser and user.is_active
assert user.email == 'admin@example.test'
assert 'initial-secret' not in command.stdout.getvalue()
assert 'ignored-secret' not in command.stdout.getvalue()
assert active_relationship_model().objects.filter(
    resource_type='angee/role', resource_id='admin', relation='member').count() == 1
assert PermissionAuditEvent.objects.filter(
    kind=PermissionAuditEvent.KIND_RELATIONSHIP_GRANT).exists()
import angee.iam
config = IAMConfig('angee.iam', angee.iam)
calls = []
context = SimpleNamespace(database='default', call=lambda *args, **kwargs: calls.append((args, kwargs)))
config.compose_provision(context)
assert calls == []
settings.ANGEE_BOOTSTRAP_ADMIN_PASSWORD = 'explicit-secret'
config.compose_provision(context)
# The command writes through the default manager: the router places the admin.
assert calls == [(('bootstrap_admin',), {})]
'''
    result = subprocess.run(
        [sys.executable, '-c', script], cwd=Path(__file__).resolve().parents[1],
        env=dict(os.environ), capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
