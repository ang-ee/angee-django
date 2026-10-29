"""Angee deployment policy around the separately owned compiler configure seam."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _probe(source: str, directory: Path) -> None:
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(('ANGEE_', 'YAMLCONF_', 'SENTRY_'))
        and key not in {'DATABASE_URL', 'CACHE_URL', 'CELERY_BROKER_URL'}
    }
    result = subprocess.run(
        [sys.executable, '-c', source, str(directory)],
        env={**environment, 'PYTHONPATH': str(ROOT / 'addons')},
        text=True, capture_output=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stderr


_SEAM = '''
import sys, os
from pathlib import Path
from types import ModuleType
root = Path(sys.argv[1])
# Only the boundary is substituted: native yamlconf/environ/Sentry owners run.
# Compiler closure, snapshot and receipt behavior are tested in its own repo.
native = ModuleType('django_compose.conf')
native.configure = lambda namespace: None
sys.modules['django_compose.conf'] = native
from angee.base.conf import configure
'''


def test_overlays_preserve_native_append_and_python_declarations(tmp_path: Path) -> None:
    _probe(_SEAM + r'''
source = root / 'source'
project = root / 'project'
source.mkdir()
project.mkdir()
(source / 'settings.yaml').write_text('VALUES:append: [source]\n')
(project / 'settings.yaml').write_text('VALUES:append: [project]\n')
final = root / 'final.yaml'
final.write_text('VALUES:append: [final]\n')
os.environ.update(ANGEE_PROJECT_DIR=str(project), YAMLCONF_CONFFILE=str(final))
authored = {'nested': {'value': 'original'}}
namespace = {'__name__':'settings', '__file__':str(source / 'settings.py'), 'VALUES':['python'], 'AUTHORED':authored}
configure(namespace)
assert namespace['VALUES'] == ['python', 'source', 'project', 'final'], namespace['VALUES']
assert authored == {'nested': {'value': 'original'}}
assert namespace['COMPOSE_ADDON_GROUP'] == 'angee.addons'
assert namespace['COMPOSE_ENV_PREFIXES'] == ('ANGEE_',)
assert set(namespace['COMPOSE_WATCH_PATHS']) == {source/'settings.yaml', project/'settings.yaml', final, project/'.env'}
''', tmp_path)


def test_dotenv_urls_pool_cache_csrf_and_process_environment_precedence(tmp_path: Path) -> None:
    _probe(_SEAM + r'''
(root / '.env').write_text('DATABASE_URL=postgres://user:pass@localhost:5432/example\nANGEE_DB_POOL=true\nANGEE_DB_POOL_MAX_SIZE=3\nCACHE_URL=locmemcache://policy\nCELERY_BROKER_URL=redis://localhost:6379/4\nANGEE_PUBLIC_ORIGIN=https://example.test\nPRESERVE=dotenv\n')
os.environ['PRESERVE'] = 'process'
namespace = {'__name__':'settings', '__file__':str(root/'settings.py')}
configure(namespace)
assert os.environ['PRESERVE'] == 'process'
database = namespace['DATABASES']['default']
assert database['ENGINE'] == 'django.db.backends.postgresql'
assert database['OPTIONS']['options'] == '-c jit=off'
assert database['OPTIONS']['pool'] == {'min_size':0, 'max_size':3, 'timeout':5.0}
assert namespace['CACHES']['default']['BACKEND'] == 'django.core.cache.backends.locmem.LocMemCache'
assert namespace['CELERY_BROKER_URL'] == 'redis://localhost:6379/4'
assert namespace['CSRF_TRUSTED_ORIGINS'] == ['https://example.test']
''', tmp_path)


def test_explicit_databases_and_caches_win_over_urls(tmp_path: Path) -> None:
    _probe(_SEAM + r'''
os.environ.update(DATABASE_URL='sqlite:///ignored.sqlite3', CACHE_URL='locmemcache://ignored')
databases = {'default':{'ENGINE':'django.db.backends.sqlite3', 'NAME':':memory:'}}
caches = {'default':{'BACKEND':'django.core.cache.backends.dummy.DummyCache'}}
namespace = {'__name__':'settings','__file__':str(root/'settings.py'),'DATABASES':databases,'CACHES':caches}
configure(namespace)
assert namespace['DATABASES'] == databases
assert namespace['CACHES'] == caches
''', tmp_path)


def test_environment_compiler_input_refuses_and_failed_yaml_keeps_watch_candidates(tmp_path: Path) -> None:
    _probe(_SEAM + r'''
from django.core.exceptions import ImproperlyConfigured
os.environ['YAMLCONF_INSTALLED_APPS'] = 'acme.invoice'
try:
    configure({'__name__':'settings','__file__':str(root/'settings.py')})
except ImproperlyConfigured as error:
    assert 'INSTALLED_APPS' in str(error)
else:
    raise AssertionError('environment selected structural roots')
os.environ.pop('YAMLCONF_INSTALLED_APPS')
(root/'settings.yaml').write_text('INSTALLED_APPS: [unterminated\n')
namespace = {'__name__':'settings','__file__':str(root/'settings.py')}
try:
    configure(namespace)
except ImproperlyConfigured:
    pass
else:
    raise AssertionError('malformed YAML accepted')
assert set(namespace['COMPOSE_WATCH_PATHS']) == {root/'settings.yaml',root/'.env'}
''', tmp_path)


def test_sentry_receives_contributed_policy_before_composition_failure_propagates(tmp_path: Path) -> None:
    _probe(_SEAM + r'''
from unittest.mock import patch
os.environ['SENTRY_DSN']='https://public@example.test/1'
def compose(namespace):
    namespace['SENTRY_DISABLED_INTEGRATIONS'] = ['policy']
    raise RuntimeError('native compose failed')
native.configure=compose
with patch('sentry_sdk.is_initialized', return_value=False), patch('sentry_sdk.init') as start:
    try:
        configure({'__name__':'settings','__file__':str(root/'settings.py')})
    except RuntimeError as error:
        assert str(error) == 'native compose failed'
    else:
        raise AssertionError('compose failure swallowed')
    start.assert_called_once_with(disabled_integrations=['policy'],send_default_pii=False,include_local_variables=False,max_request_body_size='never')
''', tmp_path)
