"""Tests for the Angee composer settings owner."""

from __future__ import annotations

import importlib
import os
import runpy
import sys
from collections.abc import Iterator
from importlib.machinery import PathFinder
from importlib.util import module_from_spec
from pathlib import Path
from types import ModuleType
from typing import Any

import django_yamlconf
import pytest
from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured

from angee.compose.autoconfig import AutoConfig
from angee.compose.composer import Composer
from angee.conf import ProjectContract, prepend_import_paths
from angee.project import PROJECT_DIR_ENV, find_project_dir, project_dir

BASE_APP = "angee.base.apps.BaseConfig"
GRAPHQL_APP = "angee.graphql.apps.GraphQLConfig"
RESOURCES_APP = "angee.resources.apps.ResourcesConfig"


def _installed_paths(installed_apps: list[object]) -> list[str]:
    """Return stable import paths for Django ``INSTALLED_APPS`` entries."""

    paths: list[str] = []
    for entry in installed_apps:
        if isinstance(entry, AppConfig):
            config_class = type(entry)
            paths.append(
                entry.name if config_class is AppConfig else f"{config_class.__module__}.{config_class.__name__}"
            )
        else:
            paths.append(str(entry))
    return paths


def _compose(tmp_path: Path) -> dict[str, Any]:
    """Return composed settings for the notes example addon."""

    settings: dict[str, Any] = {
        "INSTALLED_APPS": _default_installed_apps(tmp_path, ("example.notes",)),
        "COMPOSE_RUNTIME_DIR": tmp_path / "runtime",
    }
    settings["COMPOSE_APP_DECLARATIONS"] = "angee.conf.ManifestDeclarations"
    Composer(settings).compose_settings()
    return settings


@pytest.fixture(autouse=True)
def _generated_project_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Use the real settings artifact producer for settings-only contract cases."""

    from angee.conf import project_settings_source

    package = tmp_path / "generated" / "_settings_contract_fixture"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "settings.py").write_text(project_settings_source(authored_module="settings"), encoding="utf-8")
    (package / "settings.yaml").write_text("{}\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(package.parent))
    for name in ("_settings_contract_fixture.settings", "_settings_contract_fixture"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    importlib.import_module("_settings_contract_fixture")
    yield
    for name in ("_settings_contract_fixture.settings", "_settings_contract_fixture"):
        sys.modules.pop(name, None)


def _load_project_settings_artifact() -> ModuleType:
    """Build the real wiring for this case's authored source, then import it."""

    from angee.conf import project_settings_source

    root = project_dir()
    package = importlib.import_module("_settings_contract_fixture")
    output = Path(package.__file__).parent
    settings_file = output / "settings.py"
    settings_file.write_text(project_settings_source(
        authored_module=os.environ.get("ANGEE_PROJECT_SETTINGS", "settings"),
        source_path=os.path.relpath(root, output),
    ), encoding="utf-8")
    yaml_file = root / "settings.yaml"
    (output / "settings.yaml").write_text(yaml_file.read_text() if yaml_file.is_file() else "{}\n")
    Path(importlib.util.cache_from_source(str(settings_file))).unlink(missing_ok=True)
    importlib.invalidate_caches()
    module_name = "_settings_contract_fixture.settings"
    existing = sys.modules.get(module_name)
    return importlib.reload(existing) if existing is not None else importlib.import_module(module_name)


def _default_installed_apps(tmp_path: Path, roots: tuple[str, ...]) -> tuple[str, ...]:
    """Return framework defaults followed by the project-declared roots."""

    defaults = runpy.run_module(
        "angee.defaults",
        init_globals={"BASE_DIR": tmp_path, "INSTALLED_APPS": roots},
        run_name="__test_effective_defaults__",
    )
    return tuple(defaults["INSTALLED_APPS"])


def test_public_origin_joins_csrf_trusted_origins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ANGEE_PUBLIC_ORIGIN (set by ingress-fronted stacks) is a CSRF trusted origin."""

    monkeypatch.setenv("ANGEE_PUBLIC_ORIGIN", "https://demo.example.com")
    defaults = runpy.run_module(
        "angee.defaults",
        init_globals={"BASE_DIR": tmp_path},
        run_name="__test_effective_defaults__",
    )
    assert defaults["CSRF_TRUSTED_ORIGINS"] == ["https://demo.example.com"]


def test_project_dir_owner_discovers_explicit_manage_and_ancestor_roots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """One project-root owner serves explicit env, manage.py, and ancestor discovery."""

    explicit = tmp_path / "explicit"
    manage_project = tmp_path / "manage-project"
    nested_project = tmp_path / "nested-project"
    nested = nested_project / "web" / "src"
    explicit.mkdir()
    manage_project.mkdir()
    nested.mkdir(parents=True)
    manage_py = manage_project / "manage.py"
    manage_py.write_text("# test entrypoint\n", encoding="utf-8")
    (nested_project / "settings.yaml").write_text("SECRET_KEY: nested\n", encoding="utf-8")

    assert project_dir(environ={PROJECT_DIR_ENV: str(explicit)}, argv=[], cwd=tmp_path) == explicit.resolve()
    assert find_project_dir(environ={}, argv=[str(manage_py)], cwd=tmp_path) == manage_project.resolve()
    assert find_project_dir(environ={}, argv=["uvicorn", "angee.asgi:application"], cwd=nested) == nested_project


def test_base_is_installed_exactly_once(tmp_path: Path) -> None:
    """The model foundation is a normal installed Django app."""

    settings = _compose(tmp_path)
    installed = _installed_paths(settings["INSTALLED_APPS"])
    assert installed.count(BASE_APP) == 1


def test_resources_root_expands_framework_dependencies(tmp_path: Path) -> None:
    """A host can name resources alone and get its framework boot closure."""

    settings: dict[str, Any] = {
        "INSTALLED_APPS": _default_installed_apps(tmp_path, ("angee.resources",)),
        "COMPOSE_RUNTIME_DIR": tmp_path / "runtime",
    }
    settings["COMPOSE_APP_DECLARATIONS"] = "angee.conf.ManifestDeclarations"
    Composer(settings).compose_settings()
    installed = _installed_paths(settings["INSTALLED_APPS"])

    compose_at = installed.index("angee.compose.apps.ComposeConfig")
    base_at = installed.index(BASE_APP)
    resources_at = installed.index(RESOURCES_APP)

    assert base_at < resources_at < compose_at
    assert GRAPHQL_APP not in installed
    assert "angee.iam.apps.IAMConfig" not in installed
    assert "REBAC_UNIVERSAL_ADMIN_ROLE" not in settings
    assert "angee.jobs" not in installed
    assert "simple_history" not in installed
    assert "angee.provenance.apps.ProvenanceConfig" not in installed
    assert "CELERY_TASK_SOFT_TIME_LIMIT" not in settings
    assert "ANGEE_CHANGESET_MODEL" not in settings


def test_iam_user_is_the_default_auth_model(tmp_path: Path) -> None:
    """Composed hosts use Angee's swappable IAM user."""

    settings = _compose(tmp_path)
    installed = _installed_paths(settings["INSTALLED_APPS"])

    assert "angee.compose.apps.ComposeConfig" in installed
    assert BASE_APP in installed
    assert GRAPHQL_APP in installed
    assert RESOURCES_APP in installed
    assert settings["AUTH_USER_MODEL"] == "iam.User"
    assert "angee.iam.apps.IAMConfig" in installed


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, "angee/role:admin"),
        ({"REBAC_UNIVERSAL_ADMIN_ROLE": "example/role:owner"}, "example/role:owner"),
        ({"REBAC_UNIVERSAL_ADMIN_ROLE": None}, None),
    ],
)
def test_iam_admin_role_default_preserves_project_policy(
    tmp_path: Path,
    overrides: dict[str, str | None],
    expected: str | None,
) -> None:
    """IAM supplies its role to native REBAC unless the project chooses another policy."""

    from django.test import override_settings
    from rebac import app_settings

    settings: dict[str, Any] = {
        "INSTALLED_APPS": _default_installed_apps(tmp_path, ("angee.iam",)),
        "COMPOSE_RUNTIME_DIR": tmp_path / "runtime",
        **overrides,
    }
    settings["COMPOSE_APP_DECLARATIONS"] = "angee.conf.ManifestDeclarations"
    Composer(settings).compose_settings()

    assert settings["REBAC_UNIVERSAL_ADMIN_ROLE"] == expected
    with override_settings(REBAC_UNIVERSAL_ADMIN_ROLE=settings["REBAC_UNIVERSAL_ADMIN_ROLE"]):
        assert app_settings.REBAC_UNIVERSAL_ADMIN_ROLE == expected


def test_graphql_public_pk_is_sqid(tmp_path: Path) -> None:
    """Composed GraphQL filters expose sqid as the public pk name."""

    settings = _compose(tmp_path)

    assert settings["STRAWBERRY_DJANGO"]["DEFAULT_PK_FIELD_NAME"] == "sqid"
    assert settings["STRAWBERRY_DJANGO"]["MAP_AUTO_ID_AS_GLOBAL_ID"] is False


def test_iam_installs_csrf_middleware_for_session_graphql(tmp_path: Path) -> None:
    """IAM installs CSRF protection while bearer requests can opt out."""

    settings = _compose(tmp_path)
    middleware = settings["MIDDLEWARE"]

    session_at = middleware.index("django.contrib.sessions.middleware.SessionMiddleware")
    csrf_at = middleware.index("django.middleware.csrf.CsrfViewMiddleware")
    auth_at = middleware.index("django.contrib.auth.middleware.AuthenticationMiddleware")
    actor_at = middleware.index("rebac.middleware.ActorMiddleware")
    bearer_at = middleware.index("angee.iam.middleware.BearerTokenCsrfExemptMiddleware")

    assert session_at < csrf_at < auth_at < actor_at < bearer_at


def test_django_security_headers_lead_and_csp_middleware_applies_view_policies(tmp_path: Path) -> None:
    """Every response passes Django's SecurityMiddleware first; its CSP middleware is composed too."""

    middleware = _compose(tmp_path)["MIDDLEWARE"]

    assert middleware[0] == "django.middleware.security.SecurityMiddleware"
    assert "django.middleware.csp.ContentSecurityPolicyMiddleware" in middleware


def test_iam_auth_throttling_comes_from_axes(tmp_path: Path) -> None:
    """IAM wires login throttling at Django's authenticate/backend seam."""

    from angee.iam.autoconfig import SETTINGS

    settings = _compose(tmp_path)
    installed = _installed_paths(settings["INSTALLED_APPS"])

    assert "axes.apps.AppConfig" in installed
    assert "axes.middleware.AxesMiddleware" in settings["MIDDLEWARE"]
    assert SETTINGS["AUTHENTICATION_BACKENDS:append"] == [
        "axes.backends.AxesStandaloneBackend",
        "angee.iam.auth.ModelBackend",
    ]
    assert settings["AUTHENTICATION_BACKENDS"][0] == "axes.backends.AxesStandaloneBackend"
    assert "rebac.W001" in settings["SILENCED_SYSTEM_CHECKS"]


def test_addons_are_sorted_by_declared_dependencies(tmp_path: Path) -> None:
    """The host may list addons in any order; Their native manifests order them."""

    settings = _compose(tmp_path)
    installed = _installed_paths(settings["INSTALLED_APPS"])

    compose_at = installed.index("angee.compose.apps.ComposeConfig")
    base_at = installed.index(BASE_APP)
    graphql_at = installed.index(GRAPHQL_APP)
    iam_at = installed.index("angee.iam.apps.IAMConfig")
    resources_at = installed.index(RESOURCES_APP)
    notes_at = installed.index("example.notes")

    assert base_at < resources_at < compose_at
    assert base_at < graphql_at < iam_at
    assert resources_at < iam_at
    assert iam_at < notes_at


def test_notes_app_order_is_stable(tmp_path: Path) -> None:
    """The notes example app order is deterministic."""

    settings = _compose(tmp_path)

    assert _installed_paths(settings["INSTALLED_APPS"]) == [
        'django_yamlconf',
        'angee.apps.AngeeConfig',
        'django.contrib.contenttypes.apps.ContentTypesConfig',
        'rebac.apps.RebacConfig',
        BASE_APP,
        'channels.apps.ChannelsConfig',
        GRAPHQL_APP,
        RESOURCES_APP,
        'angee.web',
        'axes.apps.AppConfig',
        'django.contrib.auth.apps.AuthConfig',
        'django.contrib.sessions.apps.SessionsConfig',
        'angee.iam.apps.IAMConfig',
        'django_celery_beat.apps.BeatConfig',
        'angee.jobs',
        'angee.timeline.apps.TimelineConfig',
        'simple_history',
        'angee.provenance.apps.ProvenanceConfig',
        'angee.integrate.apps.IntegrateConfig',
        'angee.integrate_vcs.apps.IntegrateVcsConfig',
        'angee.mcp.apps.MCPConfig',
        'angee.operator',
        'angee.agents.apps.AgentsConfig',
        'angee.agents_integrate_anthropic',
        'angee.decisions.apps.DecisionsConfig',
        'angee.tags',
        'angee.storage.apps.StorageConfig',
        'angee.parties.apps.PartiesConfig',
        'angee.iam_integrate_oidc.apps.IAMIntegrateOidcConfig',
        'django.contrib.postgres.apps.PostgresConfig',
        'angee.messaging.apps.MessagingConfig',
        'angee.knowledge.apps.KnowledgeConfig',
        'angee.scheduling.apps.SchedulingConfig',
        'angee.projects.apps.ProjectsConfig',
        'angee.workflows.apps.WorkflowsConfig',
        'example.notes',
        'angee.compose.apps.ComposeConfig',
    ]


def test_one_app_set_orders_compose_after_source_adopters(
    tmp_path: Path,
) -> None:
    """The final phase-2 hook imports concrete models after source model hooks."""

    settings = _compose(tmp_path)
    installed = _installed_paths(settings["INSTALLED_APPS"])

    assert installed.count("angee.compose.apps.ComposeConfig") == 1
    assert installed.count(BASE_APP) == 1
    assert installed.count(GRAPHQL_APP) == 1
    assert installed.count(RESOURCES_APP) == 1
    compose_at = installed.index("angee.compose.apps.ComposeConfig")
    base_at = installed.index(BASE_APP)
    notes_at = installed.index("example.notes")
    assert base_at < notes_at < compose_at
    assert "ANGEE_BUILD" not in settings


def test_installed_apps_are_app_config_instances(tmp_path: Path) -> None:
    """Composer hands Django the AppConfig instances it already resolved."""

    settings = _compose(tmp_path)

    assert all(isinstance(entry, AppConfig) for entry in settings["INSTALLED_APPS"])


def test_rebac_strict_mode_is_explicitly_pinned(tmp_path: Path) -> None:
    """Composed hosts keep REBAC strict mode enabled by default."""

    settings = _compose(tmp_path)

    assert settings["REBAC_STRICT_MODE"] is True


def test_rebac_bare_prefetch_lint_is_quiet_by_default(tmp_path: Path) -> None:
    """Composed hosts disable the structural REBAC relation audit by default."""

    settings = _compose(tmp_path)

    assert settings["REBAC_LINT_BARE_PREFETCH"] is False


def test_rebac_bare_prefetch_lint_can_be_enabled_by_project(tmp_path: Path) -> None:
    """Project settings can still opt in to the structural REBAC relation audit."""

    settings: dict[str, Any] = {
        "INSTALLED_APPS": ("example.notes",),
        "COMPOSE_RUNTIME_DIR": tmp_path / "runtime",
        "REBAC_LINT_BARE_PREFETCH": True,
    }
    settings["COMPOSE_APP_DECLARATIONS"] = "angee.conf.ManifestDeclarations"
    Composer(settings).compose_settings()

    assert settings["REBAC_LINT_BARE_PREFETCH"] is True


def test_auth_user_model_comes_from_iam_autoconfig(
    tmp_path: Path,
) -> None:
    """The run user model is contributed by IAM, not hardcoded in compose."""

    from angee.iam.autoconfig import SETTINGS

    settings = _compose(tmp_path)

    assert SETTINGS["AUTH_USER_MODEL"] == "iam.User"
    assert SETTINGS["AUTHENTICATION_BACKENDS:append"] == [
        "axes.backends.AxesStandaloneBackend",
        "angee.iam.auth.ModelBackend",
    ]
    assert settings["AUTH_USER_MODEL"] == "iam.User"


def test_graphql_ide_default_is_debug_only(tmp_path: Path) -> None:
    """GraphQL owns its IDE default and only enables it for DEBUG projects."""

    from angee.graphql.autoconfig import settings as graphql_settings

    settings = _compose(tmp_path)

    assert graphql_settings({"DEBUG": True})["ANGEE_GRAPHQL_IDE"] == "graphiql"
    assert settings["ANGEE_GRAPHQL_IDE"] is None

    debug_settings: dict[str, Any] = {
        "INSTALLED_APPS": ("angee.graphql",),
        "COMPOSE_RUNTIME_DIR": tmp_path / "debug-runtime",
        "DEBUG": True,
    }
    debug_settings["COMPOSE_APP_DECLARATIONS"] = "angee.conf.ManifestDeclarations"
    Composer(debug_settings).compose_settings()

    assert debug_settings["ANGEE_GRAPHQL_IDE"] == "graphiql"


def test_graphql_uses_channels_redis_when_redis_url_is_set(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A shared Redis channel layer replaces the dev-only in-memory layer."""

    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6379/0")

    settings = _compose(tmp_path)

    assert settings["CHANNEL_LAYERS"]["default"] == {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {"hosts": ["redis://127.0.0.1:6379/0"], "group_expiry": 900},
    }


def test_jobs_use_celery_broker_url_from_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Celery uses the job broker URL injected by the stack."""

    monkeypatch.setenv("CELERY_BROKER_URL", "redis://127.0.0.1:6379/1")

    settings = _compose(tmp_path)

    assert settings["CELERY_BROKER_URL"] == "redis://127.0.0.1:6379/1"
    assert settings["CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP"] is True


@pytest.mark.parametrize("environment", [False, True])
def test_autoconfig_environment_precedence_is_shared_by_declared_and_derived_settings(monkeypatch, environment):
    """Composer environment overrides project values; bare settings retain project values."""

    monkeypatch.setenv("CELERY_BROKER_URL", "redis://broker.example.test/1")
    monkeypatch.setenv("CHANNEL_REDIS_URL", "redis://channels.example.test/2")
    monkeypatch.setenv("ANGEE_OPERATOR_URL", "https://operator.example.test")
    monkeypatch.setenv("ANYMAIL", '{"MAILGUN_API_KEY": "environment"}')
    namespace = {
        "INSTALLED_APPS": ["angee.jobs", "angee.graphql", "angee.operator", "angee.messaging"],
        "COMPOSE_ENV_PREFIXES": ("ANGEE_",),
        "CELERY_BROKER_URL": "redis://project.example.test/0",
        "CHANNEL_REDIS_URL": "redis://project.example.test/1",
        "ANGEE_OPERATOR_URL": "https://project.example.test",
        "ANYMAIL": {"MAILGUN_API_KEY": "project"},
    }

    AutoConfig.apply_installed(namespace, environment=environment)

    assert namespace["CELERY_BROKER_URL"] == (
        "redis://broker.example.test/1" if environment else "redis://project.example.test/0"
    )
    assert namespace["CHANNEL_LAYERS"]["default"]["CONFIG"]["hosts"] == [
        "redis://channels.example.test/2" if environment else "redis://project.example.test/1"
    ]
    assert namespace["ANGEE_OPERATOR_URL"] == (
        "https://operator.example.test" if environment else "https://project.example.test"
    )
    assert namespace["ANYMAIL"] == {"MAILGUN_API_KEY": "environment" if environment else "project"}


@pytest.mark.parametrize(
    ("project", "environment", "expected"),
    [(None, None, 60), (30, None, 30), (30, "15", 15)],
)
def test_web_statement_timeout_is_declared_with_project_and_environment_overrides(
    monkeypatch: pytest.MonkeyPatch,
    project: int | None,
    environment: str | None,
    expected: int,
) -> None:
    """The Angee host declares the web statement bound; project/env values override it."""

    if environment is None:
        monkeypatch.delenv("ANGEE_WEB_STATEMENT_TIMEOUT", raising=False)
    else:
        monkeypatch.setenv("ANGEE_WEB_STATEMENT_TIMEOUT", environment)
    namespace: dict[str, Any] = {"INSTALLED_APPS": ["angee"], "COMPOSE_ENV_PREFIXES": ("ANGEE_",)}
    if project is not None:
        namespace["ANGEE_WEB_STATEMENT_TIMEOUT"] = project

    AutoConfig.apply_installed(namespace)

    assert float(namespace["ANGEE_WEB_STATEMENT_TIMEOUT"]) == expected


def test_data_dir_is_host_owned_not_composed(tmp_path: Path) -> None:
    """Runtime.settings no longer couriers ANGEE_DATA_DIR; the host owns it."""

    settings = _compose(tmp_path)

    assert "ANGEE_DATA_DIR" not in settings


def test_runtime_settings_makes_runtime_package_importable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The Angee settings owner wires the generated runtime import path."""

    runtime_dir = tmp_path / "generated" / "runtime"
    monkeypatch.setattr(sys, "path", [path for path in sys.path if path != str(runtime_dir.parent)])

    settings: dict[str, Any] = {
        "INSTALLED_APPS": ("angee.resources",),
        "COMPOSE_RUNTIME_DIR": runtime_dir,
    }
    settings["COMPOSE_APP_DECLARATIONS"] = "angee.conf.ManifestDeclarations"
    Composer(settings).compose_settings()

    assert sys.path[0] == str(runtime_dir.parent)


def test_runtime_dir_string_matches_normalized_composer_path(tmp_path: Path) -> None:
    """String runtime-dir values do not conflict with Composer's normalized path."""

    runtime_dir = tmp_path / "runtime"
    settings: dict[str, Any] = {
        "INSTALLED_APPS": ("angee.resources",),
        "COMPOSE_RUNTIME_DIR": str(runtime_dir),
    }
    settings["COMPOSE_APP_DECLARATIONS"] = "angee.conf.ManifestDeclarations"
    Composer(settings).compose_settings()

    assert settings["COMPOSE_RUNTIME_DIR"] == runtime_dir


def test_compose_settings_module_reads_project_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The Django settings module can be Angee-owned and project-fed."""

    project = ModuleType("settings")
    project.__file__ = str(tmp_path / "settings.py")
    project.SECRET_KEY = "bridge-secret"  # type: ignore[attr-defined]
    project.DEBUG = True  # type: ignore[attr-defined]
    project.ALLOWED_HOSTS = ["*"]  # type: ignore[attr-defined]
    project.DATABASES = {  # type: ignore[attr-defined]
        "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"},
    }
    project.ANGEE_DATA_DIR = tmp_path / "data"  # type: ignore[attr-defined]
    project.INSTALLED_APPS = ("angee.resources",)  # type: ignore[attr-defined]
    project.COMPOSE_RUNTIME_DIR = tmp_path / "runtime"  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, project.__name__, project)
    monkeypatch.setenv("ANGEE_PROJECT_DIR", str(tmp_path))

    compose_settings = _load_project_settings_artifact()

    assert compose_settings.SECRET_KEY == "bridge-secret"
    assert compose_settings.DEBUG is True
    assert compose_settings.ANGEE_DATA_DIR == tmp_path / "data"
    assert compose_settings.COMPOSE_RUNTIME_DIR == Path(compose_settings.__file__).resolve().parent
    assert compose_settings.ROOT_URLCONF == "angee.urls"
    assert compose_settings.ASGI_APPLICATION == "angee.asgi.application"
    installed = _installed_paths(compose_settings.INSTALLED_APPS)
    assert "angee.compose.apps.ComposeConfig" in installed
    assert RESOURCES_APP in installed
    assert not hasattr(compose_settings, "ANGEE_RUNTIME")


def test_compose_settings_module_reads_named_project_settings_module(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """ANGEE_PROJECT_SETTINGS selects a project-owned Python settings module."""

    package = tmp_path / "project"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "settings.py").write_text(
        "\n".join(
            [
                "SECRET_KEY = 'package-secret'",
                "INSTALLED_APPS = ('angee.resources',)",
                f"COMPOSE_RUNTIME_DIR = {str(tmp_path / 'runtime')!r}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delitem(sys.modules, "settings", raising=False)
    monkeypatch.delitem(sys.modules, "project", raising=False)
    monkeypatch.delitem(sys.modules, "project.settings", raising=False)
    monkeypatch.setenv("ANGEE_PROJECT_DIR", str(tmp_path))
    monkeypatch.setenv("ANGEE_PROJECT_SETTINGS", "project.settings")

    compose_settings = _load_project_settings_artifact()

    assert compose_settings.SECRET_KEY == "package-secret"
    assert compose_settings.COMPOSE_RUNTIME_DIR == Path(compose_settings.__file__).resolve().parent
    installed = _installed_paths(compose_settings.INSTALLED_APPS)
    assert RESOURCES_APP in installed


def test_compose_settings_module_does_not_import_external_settings_module(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """ANGEE_PROJECT_SETTINGS must resolve inside the configured project."""

    project = tmp_path / "project"
    outside = tmp_path / "outside"
    marker = tmp_path / "imported"
    project.mkdir()
    outside.mkdir()
    (outside / "outside_settings.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('imported', encoding='utf-8')\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(outside))
    monkeypatch.delitem(sys.modules, "settings", raising=False)
    monkeypatch.delitem(sys.modules, "outside_settings", raising=False)
    monkeypatch.setenv("ANGEE_PROJECT_DIR", str(project))
    monkeypatch.setenv("ANGEE_PROJECT_SETTINGS", "outside_settings")

    with pytest.raises(ImproperlyConfigured, match="settings.py or settings.yaml"):
        _load_project_settings_artifact()

    assert not marker.exists()


def test_compose_settings_module_layers_yaml_over_python_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A project may keep Python settings and a settings.yaml overlay."""

    manage_py = tmp_path / "manage.py"
    manage_py.write_text("# test entrypoint\n", encoding="utf-8")
    (tmp_path / "settings.py").write_text(
        "\n".join(
            [
                "SECRET_KEY = 'python-secret'",
                "DEBUG = False",
                "INSTALLED_APPS = ('angee.resources',)",
                f"COMPOSE_RUNTIME_DIR = {str(tmp_path / 'runtime')!r}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "settings.yaml").write_text(
        "\n".join(
            [
                "SECRET_KEY: yaml-secret",
                "DEBUG: true",
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delitem(sys.modules, "settings", raising=False)
    monkeypatch.setattr(sys, "argv", [str(manage_py)])

    compose_settings = _load_project_settings_artifact()

    assert compose_settings.SECRET_KEY == "yaml-secret"
    assert compose_settings.DEBUG is True


def test_compose_settings_module_reads_settings_yaml(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """settings.yaml can declare only composition facts and project overrides."""

    monkeypatch.delenv("DATABASE_URL", raising=False)
    manage_py = tmp_path / "manage.py"
    manage_py.write_text("# test entrypoint\n", encoding="utf-8")
    (tmp_path / "addons").mkdir()
    (tmp_path / "settings.yaml").write_text(
        "\n".join(
            [
                "SECRET_KEY: yaml-secret",
                "DEBUG: true",
                "INSTALLED_APPS:",
                "  - angee.resources",
                "ANGEE_ADDON_DIRS:",
                '  - "{BASE_DIR}/addons"',
                'COMPOSE_RUNTIME_DIR: "{BASE_DIR}/generated/runtime"',
                'ANGEE_DATA_DIR: "{BASE_DIR}/state"',
                "STATIC_URL: /assets/",
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delitem(sys.modules, "settings", raising=False)
    monkeypatch.setattr(
        sys,
        "path",
        [path for path in sys.path if path not in {str(tmp_path), str(tmp_path / "addons")}],
    )
    monkeypatch.setattr(sys, "argv", [str(manage_py)])

    compose_settings = _load_project_settings_artifact()

    assert compose_settings.SECRET_KEY == "yaml-secret"
    assert compose_settings.BASE_DIR == tmp_path
    assert compose_settings.ANGEE_ADDON_DIRS == (tmp_path / "addons",)
    assert compose_settings.COMPOSE_RUNTIME_DIR == Path(compose_settings.__file__).resolve().parent
    assert compose_settings.ANGEE_DATA_DIR == tmp_path / "state"
    assert compose_settings.DATABASES["default"]["NAME"] == tmp_path / "state" / "db.sqlite3"
    assert compose_settings.STATIC_URL == "/assets/"
    assert compose_settings.STATIC_ROOT == tmp_path / "state" / "static"
    assert compose_settings.MEDIA_URL == "/media/"
    assert compose_settings.MEDIA_ROOT == tmp_path / "state" / "media"
    assert str(tmp_path / "addons") in sys.path
    assert str(tmp_path) in sys.path
    assert sys.path.index(str(tmp_path / "addons")) < sys.path.index(str(tmp_path))
    installed = _installed_paths(compose_settings.INSTALLED_APPS)
    assert "django_yamlconf" in installed
    assert RESOURCES_APP in installed
    assert installed.index(RESOURCES_APP) < installed.index("angee.compose.apps.ComposeConfig")


def test_prepend_import_paths_refreshes_the_loaded_foundation_package(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Configured addon roots join ``angee.__path__`` after settings imports the foundation."""

    import angee

    addon_root = tmp_path / "addons"
    addon_portion = addon_root / "angee"
    addon_portion.mkdir(parents=True)
    foundation_paths = list(angee.__path__)
    monkeypatch.setattr(angee, "__path__", foundation_paths.copy())
    monkeypatch.setattr(sys, "path", [path for path in sys.path if path != str(addon_root)])

    prepend_import_paths((addon_root,))

    assert angee.__path__[0] == str(addon_portion)
    assert all(path in angee.__path__ for path in foundation_paths)


def test_prepend_import_paths_deduplicates_symlinked_namespace_locations(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """One physical addon remains one native Django app location through a symlink."""

    import angee

    installed = tmp_path / "site-packages"
    addon = installed / "angee" / "cold_namespace"
    addon.mkdir(parents=True)
    external_addon = installed / "cold_consumer" / "accounting"
    external_addon.mkdir(parents=True)
    linked = tmp_path / "linked-site-packages"
    linked.symlink_to(installed, target_is_directory=True)
    distinct = tmp_path / "other-addons" / "angee"
    distinct.mkdir(parents=True)
    monkeypatch.setattr(angee, "__path__", [str(linked / "angee"), str(distinct)])
    monkeypatch.setattr(sys, "path", [str(linked), *sys.path])

    prepend_import_paths((installed, linked))
    prepend_import_paths((installed,))

    assert angee.__path__ == [str((installed / "angee").resolve()), str(distinct.resolve())]
    spec = PathFinder.find_spec("angee.cold_namespace", angee.__path__)
    assert spec is not None
    config = AppConfig("angee.cold_namespace", module_from_spec(spec))
    assert Path(config.path) == addon.resolve()
    assert sum(Path(entry).resolve() == installed.resolve() for entry in sys.path) == 1
    consumer = PathFinder.find_spec("cold_consumer", sys.path)
    assert consumer is not None
    monkeypatch.setitem(sys.modules, "cold_consumer", module_from_spec(consumer))
    accounting = PathFinder.find_spec("cold_consumer.accounting", consumer.submodule_search_locations)
    assert accounting is not None
    consumer_config = AppConfig("cold_consumer.accounting", module_from_spec(accounting))
    assert Path(consumer_config.path) == external_addon.resolve()


def test_compose_settings_module_uses_configured_addon_dirs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """settings.yaml owns the addon import directories."""

    manage_py = tmp_path / "manage.py"
    manage_py.write_text("# test entrypoint\n", encoding="utf-8")
    addon_dir = tmp_path / "plugins"
    _write_addon(addon_dir, "alpha")
    (tmp_path / "settings.yaml").write_text(
        "\n".join(
            [
                "SECRET_KEY: yaml-secret",
                "INSTALLED_APPS:",
                "  - alpha",
                "ANGEE_ADDON_DIRS:",
                '  - "{BASE_DIR}/plugins"',
                'COMPOSE_RUNTIME_DIR: "{BASE_DIR}/runtime"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delitem(sys.modules, "settings", raising=False)
    monkeypatch.setattr(sys, "argv", [str(manage_py)])

    compose_settings = _load_project_settings_artifact()

    assert str(addon_dir) in sys.path
    assert "alpha.apps.TestConfig" in _installed_paths(compose_settings.INSTALLED_APPS)


def test_compose_settings_module_honors_yamlconf_final_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """YAMLCONF_CONFFILE is an explicit final project override."""

    manage_py = tmp_path / "manage.py"
    manage_py.write_text("# test entrypoint\n", encoding="utf-8")
    (tmp_path / "settings.yaml").write_text(
        "\n".join(
            [
                "SECRET_KEY: yaml-secret",
                "INSTALLED_APPS:",
                "  - angee.resources",
                'COMPOSE_RUNTIME_DIR: "{BASE_DIR}/runtime"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    final_settings = tmp_path / "local.yaml"
    final_settings.write_text("SECRET_KEY: final-secret\n", encoding="utf-8")
    monkeypatch.delitem(sys.modules, "settings", raising=False)
    monkeypatch.setattr(sys, "argv", [str(manage_py)])
    monkeypatch.setenv("YAMLCONF_CONFFILE", str(final_settings))

    compose_settings = _load_project_settings_artifact()

    assert compose_settings.SECRET_KEY == "final-secret"
    assert "SECRET_KEY" not in compose_settings.ANGEE_PROJECT_YAML_SETTINGS
    assert "INSTALLED_APPS" in compose_settings.ANGEE_PROJECT_YAML_SETTINGS
    configs = {config.name: config for config in compose_settings.INSTALLED_APPS}
    assert configs["angee.resources"].compose_root_declaration == "angee.resources"
    assert configs["django.contrib.contenttypes"].compose_root_declaration is None


def test_compose_settings_module_honors_yamlconf_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """YAMLCONF_* values are handled by django-yamlconf."""

    manage_py = tmp_path / "manage.py"
    manage_py.write_text("# test entrypoint\n", encoding="utf-8")
    (tmp_path / "settings.yaml").write_text(
        "\n".join(
            [
                "SECRET_KEY: yaml-secret",
                "DEBUG: true",
                "INSTALLED_APPS:",
                "  - angee.resources",
                'COMPOSE_RUNTIME_DIR: "{BASE_DIR}/runtime"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delitem(sys.modules, "settings", raising=False)
    monkeypatch.setattr(sys, "argv", [str(manage_py)])
    monkeypatch.setenv("YAMLCONF_SECRET_KEY", "env-secret")

    compose_settings = _load_project_settings_artifact()

    assert compose_settings.SECRET_KEY == "env-secret"


def test_compose_settings_module_honors_yamlconf_jsonenv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """YAMLCONF_* stays string unless the setting declares :jsonenv."""

    manage_py = tmp_path / "manage.py"
    manage_py.write_text("# test entrypoint\n", encoding="utf-8")
    (tmp_path / "settings.yaml").write_text(
        "\n".join(
            [
                "SECRET_KEY: yaml-secret",
                "DEBUG:jsonenv: true",
                "ALLOWED_HOSTS:jsonenv: true",
                "INSTALLED_APPS:",
                "  - angee.resources",
                'COMPOSE_RUNTIME_DIR: "{BASE_DIR}/runtime"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delitem(sys.modules, "settings", raising=False)
    monkeypatch.setattr(sys, "argv", [str(manage_py)])
    monkeypatch.setenv("YAMLCONF_DEBUG", "false")
    monkeypatch.setenv("YAMLCONF_ALLOWED_HOSTS", '["example.test"]')

    compose_settings = _load_project_settings_artifact()

    assert compose_settings.DEBUG is False
    assert compose_settings.ALLOWED_HOSTS == ["example.test"]


def test_compose_settings_module_rejects_bad_yaml_reference(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """django-yamlconf logged expansion errors fail composition."""

    manage_py = tmp_path / "manage.py"
    manage_py.write_text("# test entrypoint\n", encoding="utf-8")
    (tmp_path / "settings.yaml").write_text(
        "\n".join(
            [
                "SECRET_KEY: yaml-secret",
                "INSTALLED_APPS:",
                "  - angee.resources",
                'COMPOSE_RUNTIME_DIR: "{BASE_DR}/runtime"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delitem(sys.modules, "settings", raising=False)
    monkeypatch.setattr(sys, "argv", [str(manage_py)])

    with pytest.raises(ImproperlyConfigured, match="Undefined attribute"):
        _load_project_settings_artifact()


def test_compose_settings_module_rejects_malformed_yaml(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """django-yamlconf logged parser errors fail composition."""

    manage_py = tmp_path / "manage.py"
    manage_py.write_text("# test entrypoint\n", encoding="utf-8")
    (tmp_path / "settings.yaml").write_text("SECRET_KEY: [\n", encoding="utf-8")
    monkeypatch.delitem(sys.modules, "settings", raising=False)
    monkeypatch.setattr(sys, "argv", [str(manage_py)])

    with pytest.raises(ImproperlyConfigured, match="Failed to load"):
        _load_project_settings_artifact()


def test_compose_settings_module_ignores_ancestor_settings_yaml(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An ancestor settings.yaml never leaks into a nested host project.

    Hosting the project inside another Angee instance puts that instance's own
    ``settings.yaml`` on the path from the project root to the filesystem root.
    The compose must bound its sources to the project's own file: it succeeds
    and the ancestor value is neither applied nor allowed to override.
    """

    project = tmp_path / "project"
    project.mkdir()
    (tmp_path / "settings.yaml").write_text("SECRET_KEY: leaked-secret\n", encoding="utf-8")
    manage_py = project / "manage.py"
    manage_py.write_text("# test entrypoint\n", encoding="utf-8")
    (project / "settings.yaml").write_text(
        "\n".join(
            [
                "SECRET_KEY: project-secret",
                "INSTALLED_APPS:",
                "  - angee.resources",
                'COMPOSE_RUNTIME_DIR: "{BASE_DIR}/runtime"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delitem(sys.modules, "settings", raising=False)
    monkeypatch.setattr(sys, "argv", [str(manage_py)])

    compose_settings = _load_project_settings_artifact()

    assert compose_settings.SECRET_KEY == "project-secret"
    assert compose_settings.BASE_DIR == project


def test_host_keeps_native_bootstrap_variables_out_of_django_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """New native bootstrap names expand without leaking into the host namespace."""

    native_bootstrap = django_yamlconf.bootstrap_attributes

    def bootstrap(base_dir: str) -> dict[str, Any]:
        attributes: dict[str, Any] = native_bootstrap(base_dir)
        django_yamlconf.add_attr_info(attributes, "LIBRARY_BOOTSTRAP_ONLY", "native-value")
        return attributes

    monkeypatch.setattr(django_yamlconf, "bootstrap_attributes", bootstrap)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setenv("ANGEE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("ANGEE_PROJECT_SETTINGS", raising=False)
    monkeypatch.delenv("YAMLCONF_CONFFILE", raising=False)
    project_settings = ModuleType("settings")
    project_settings.__file__ = str(tmp_path / "settings.py")
    monkeypatch.setitem(sys.modules, "settings", project_settings)
    (tmp_path / "settings.yaml").write_text('SECRET_KEY: "{LIBRARY_BOOTSTRAP_ONLY}"\n', encoding="utf-8")

    namespace: dict[str, Any] = {}
    ProjectContract(namespace).load()

    assert namespace["SECRET_KEY"] == "native-value"
    assert namespace["BASE_DIR"] == tmp_path
    assert "LIBRARY_BOOTSTRAP_ONLY" not in namespace


def test_reject_unexpected_yamlconf_source_fails_fast(tmp_path: Path) -> None:
    """The provenance guard still rejects a foreign yamlconf source.

    With the bounded loader no ancestor cascade can reach the guard, so it
    becomes a fail-fast assertion: any attribute whose recorded source is not
    the project file, the ``YAMLCONF_CONFFILE`` overlay, or an internal/env
    sentinel is a bug the composer must refuse.
    """

    project_settings = ModuleType("settings")
    project_settings.__file__ = str(tmp_path / "settings.py")
    setattr(
        project_settings,
        "_YAMLCONF_ATTRIBUTES",
        {"SECRET_KEY": {"source": str(tmp_path.parent / "settings.yaml"), "history": ()}},
    )

    with pytest.raises(ImproperlyConfigured, match="Unexpected django-yamlconf source"):
        ProjectContract({})._reject_unexpected_sources(
            project_settings, tmp_path, source_yaml=tmp_path / "settings.yaml",
        )


def test_compose_settings_module_uses_project_dir_for_non_manage_entrypoints(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Direct ASGI imports can point compose settings at the project root."""

    (tmp_path / "addons").mkdir()
    (tmp_path / "settings.yaml").write_text(
        "\n".join(
            [
                "SECRET_KEY: asgi-secret",
                "INSTALLED_APPS:",
                "  - angee.resources",
                'COMPOSE_RUNTIME_DIR: "{BASE_DIR}/runtime"',
                'ANGEE_DATA_DIR: "{BASE_DIR}/state"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delitem(sys.modules, "settings", raising=False)
    monkeypatch.setattr(
        sys,
        "path",
        [path for path in sys.path if path not in {str(tmp_path), str(tmp_path / "addons")}],
    )
    monkeypatch.setattr(sys, "argv", ["uvicorn", "angee.asgi:application"])
    monkeypatch.setenv("ANGEE_PROJECT_DIR", str(tmp_path))

    compose_settings = _load_project_settings_artifact()

    assert compose_settings.SECRET_KEY == "asgi-secret"
    assert compose_settings.BASE_DIR == tmp_path
    assert compose_settings.COMPOSE_RUNTIME_DIR == Path(compose_settings.__file__).resolve().parent
    assert compose_settings.ROOT_URLCONF == "angee.urls"
    assert compose_settings.ASGI_APPLICATION == "angee.asgi.application"
    assert str(tmp_path / "addons") in sys.path


def test_compose_settings_module_discovers_project_ancestor_for_non_manage_entrypoints(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The shared project-dir owner lets ASGI-style imports run from child dirs."""

    project = tmp_path / "project"
    nested = project / "web" / "src"
    nested.mkdir(parents=True)
    (project / "settings.yaml").write_text(
        "\n".join(
            [
                "SECRET_KEY: nested-asgi-secret",
                "INSTALLED_APPS:",
                "  - angee.resources",
                'COMPOSE_RUNTIME_DIR: "{BASE_DIR}/runtime"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(nested)
    monkeypatch.delitem(sys.modules, "settings", raising=False)
    monkeypatch.delenv("ANGEE_PROJECT_DIR", raising=False)
    monkeypatch.setattr(sys, "argv", ["uvicorn", "angee.asgi:application"])

    compose_settings = _load_project_settings_artifact()

    assert compose_settings.SECRET_KEY == "nested-asgi-secret"
    assert compose_settings.BASE_DIR == project
    assert compose_settings.COMPOSE_RUNTIME_DIR == Path(compose_settings.__file__).resolve().parent


def test_compose_settings_rejects_cwd_without_project_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A random working directory must not become the project root."""

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANGEE_PROJECT_DIR", raising=False)
    monkeypatch.delitem(sys.modules, "settings", raising=False)
    monkeypatch.setattr(sys, "argv", ["uvicorn", "angee.asgi:application"])

    with pytest.raises(ImproperlyConfigured, match="ANGEE_PROJECT_DIR"):
        _load_project_settings_artifact()

    assert not (tmp_path / "db.sqlite3").exists()
    assert not (tmp_path / ".angee").exists()


def test_defaults_module_requires_compose_seed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The defaults module must not derive BASE_DIR from cwd when run directly."""

    monkeypatch.chdir(tmp_path)

    with pytest.raises(ImproperlyConfigured, match="project settings loader"):
        runpy.run_module("angee.defaults", run_name="__test_defaults__")

    assert not (tmp_path / "db.sqlite3").exists()
    assert not (tmp_path / ".angee").exists()


def test_defaults_module_seeds_compose_installed_app(tmp_path: Path) -> None:
    """Defaults seed the composer app before project app roots."""

    defaults = runpy.run_module(
        "angee.defaults",
        init_globals={"BASE_DIR": tmp_path, "INSTALLED_APPS": ("angee.resources",)},
        run_name="__test_defaults__",
    )

    assert defaults["INSTALLED_APPS"] == [
        "django_yamlconf", "angee.compose", "angee", "angee.base", "angee.resources",
    ]
    assert defaults["USE_TZ"] is True
    assert defaults["DEFAULT_AUTO_FIELD"] == "django.db.models.BigAutoField"
    assert defaults["COMPOSE_RUNTIME_MODULE"] == "runtime"
    assert defaults["ANGEE_ADDON_DIRS"] == (tmp_path / "addons",)


def _write_addon(
    root: Path,
    name: str,
    *,
    depends_on: object = ("angee.compose",),
    autoconfig: str = "SETTINGS = {}\n",
    label: str | None = None,
) -> None:
    """Write a small importable addon package for settings tests."""

    package = root / name
    sys.modules.pop(name, None)
    sys.modules.pop(f"{name}.apps", None)
    sys.modules.pop(f"{name}.autoconfig", None)
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "apps.py").write_text(
        "\n".join(
            [
                "from django.apps import AppConfig",
                "",
                "class TestConfig(AppConfig):",
                "    default = True",
                f"    name = {name!r}",
                *([f"    label = {label!r}"] if label is not None else []),
                "",
            ]
        ),
        encoding="utf-8",
    )
    (package / "addon.toml").write_text(_addon_toml(name, depends_on), encoding="utf-8")
    (package / "autoconfig.py").write_text(autoconfig, encoding="utf-8")


def _addon_toml(name: str, depends_on: object) -> str:
    """Render a temp addon's addon.toml manifest with its declared depends_on.

    A bare string is written as a TOML string (not a one-element array), so the
    reader's bare-string coercion is exercised end to end rather than pre-normalized
    here.
    """

    if isinstance(depends_on, str):
        rendered = f'"{depends_on}"'
    else:
        items = []
        for item in depends_on:
            if isinstance(item, str):
                items.append(f'"{item}"')
            elif isinstance(item, bool):
                items.append("true" if item else "false")
            else:
                items.append(repr(item))
        rendered = "[" + ", ".join(items) + "]"
    return f'[addon]\nname = "{name}"\ndepends_on = {rendered}\n'


def _addon_test_config(name: str) -> AppConfig:
    """Return an ``AppConfig`` for a temp addon package.

    The temp package carries its contract in a co-located ``addon.toml`` (see
    ``_write_addon``); the composer reads it from ``config.path``, and the manifest's
    presence is what marks the app as an addon.
    """

    module = importlib.import_module(name)
    config_cls = getattr(importlib.import_module(f"{name}.apps"), "TestConfig")
    return config_cls(name, module)


def test_addon_autoconfig_applies_setting_fragments(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Runtime applies addon autoconfig modules while composing settings."""

    _write_addon(
        tmp_path,
        "alpha",
        autoconfig=(
            "SETTINGS = {\n"
            "    'MIDDLEWARE:append': ['alpha.middleware.One'],\n"
            "    'ALPHA_SETTING': 'ok',\n"
            "    'CHANNEL_LAYERS:append': {'alpha': {'BACKEND': 'tests.Layer'}},\n"
            "}\n"
        ),
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    settings: dict[str, Any] = {
        "INSTALLED_APPS": ("alpha",),
        "COMPOSE_RUNTIME_DIR": tmp_path / "runtime",
    }
    settings["COMPOSE_APP_DECLARATIONS"] = "angee.conf.ManifestDeclarations"
    Composer(settings).compose_settings()

    assert settings["ALPHA_SETTING"] == "ok"
    assert settings["CHANNEL_LAYERS"]["alpha"]["BACKEND"] == "tests.Layer"
    assert settings["MIDDLEWARE"] == [
        "alpha.middleware.One",
    ]


def test_addon_autoconfig_merges_resource_source_classes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Dotted autoconfig keys append resource sources to the settings registry."""

    _write_addon(
        tmp_path,
        "alpha",
        autoconfig=("SETTINGS = {\n    'ANGEE_RESOURCE_SOURCE_CLASSES.url': 'alpha.sources.UrlSource',\n}\n"),
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    settings: dict[str, Any] = {
        "INSTALLED_APPS": ("alpha",),
        "COMPOSE_RUNTIME_DIR": tmp_path / "runtime",
        "ANGEE_RESOURCE_SOURCE_CLASSES": {
            "path": "angee.resources.sources.PathSource",
        },
    }
    settings["COMPOSE_APP_DECLARATIONS"] = "angee.conf.ManifestDeclarations"
    Composer(settings).compose_settings()

    assert settings["ANGEE_RESOURCE_SOURCE_CLASSES"] == {
        "path": "angee.resources.sources.PathSource",
        "url": "alpha.sources.UrlSource",
    }


def test_addon_autoconfig_merges_sequences_in_dependency_order(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Sequence settings contributed by addons follow depends_on order."""

    _write_addon(
        tmp_path,
        "alpha",
        autoconfig=(
            "SETTINGS = {\n"
            "    'MIDDLEWARE:append': ['alpha.middleware.One'],\n"
            "    'AUTHENTICATION_BACKENDS:append': ['alpha.auth.Backend'],\n"
            "}\n"
        ),
    )
    _write_addon(
        tmp_path,
        "beta",
        depends_on=("alpha",),
        autoconfig=(
            "SETTINGS = {\n"
            "    'MIDDLEWARE:append': ['beta.middleware.Two'],\n"
            "    'AUTHENTICATION_BACKENDS:append': ['beta.auth.Backend'],\n"
            "}\n"
        ),
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    settings: dict[str, Any] = {
        "INSTALLED_APPS": ("beta",),
        "COMPOSE_RUNTIME_DIR": tmp_path / "runtime",
    }
    settings["COMPOSE_APP_DECLARATIONS"] = "angee.conf.ManifestDeclarations"
    Composer(settings).compose_settings()

    assert settings["MIDDLEWARE"] == [
        "alpha.middleware.One",
        "beta.middleware.Two",
    ]
    assert settings["AUTHENTICATION_BACKENDS"] == [
        "alpha.auth.Backend",
        "beta.auth.Backend",
    ]


def test_depends_on_treats_bare_string_as_one_app(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A bare-string ``depends_on`` names one app, not its chars."""

    _write_addon(tmp_path, "alpha", depends_on="angee.compose")
    monkeypatch.syspath_prepend(str(tmp_path))

    settings: dict[str, Any] = {
        "INSTALLED_APPS": ("alpha",),
        "COMPOSE_RUNTIME_DIR": tmp_path / "runtime",
    }
    settings["COMPOSE_APP_DECLARATIONS"] = "angee.conf.ManifestDeclarations"
    Composer(settings).compose_settings()

    assert "alpha.apps.TestConfig" in _installed_paths(settings["INSTALLED_APPS"])


def test_depends_on_rejects_non_string_items(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Dependency declarations fail at the AppConfig contract boundary."""

    _write_addon(tmp_path, "alpha", depends_on=(1,))
    monkeypatch.syspath_prepend(str(tmp_path))

    settings: dict[str, Any] = {
        "INSTALLED_APPS": ("alpha",),
        "COMPOSE_RUNTIME_DIR": tmp_path / "runtime",
    }
    with pytest.raises(ImproperlyConfigured, match="depends_on"):
        settings["COMPOSE_APP_DECLARATIONS"] = "angee.conf.ManifestDeclarations"
        Composer(settings).compose_settings()


def test_root_installed_apps_keep_project_order(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Unconstrained root apps keep the project's declared order."""

    _write_addon(tmp_path, "alpha")
    _write_addon(tmp_path, "beta")
    monkeypatch.syspath_prepend(str(tmp_path))

    settings: dict[str, Any] = {
        "INSTALLED_APPS": ("beta", "alpha"),
        "COMPOSE_RUNTIME_DIR": tmp_path / "runtime",
    }
    settings["COMPOSE_APP_DECLARATIONS"] = "angee.conf.ManifestDeclarations"
    Composer(settings).compose_settings()
    installed = _installed_paths(settings["INSTALLED_APPS"])

    assert installed.index("beta.apps.TestConfig") < installed.index("alpha.apps.TestConfig")


def test_later_root_app_labels_are_available_to_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A root may depend on another later root by Django app label."""

    _write_addon(tmp_path, "alpha", depends_on=("beta",))
    _write_addon(tmp_path, "project_beta", label="beta")
    monkeypatch.syspath_prepend(str(tmp_path))

    settings: dict[str, Any] = {
        "INSTALLED_APPS": ("alpha", "project_beta"),
        "COMPOSE_RUNTIME_DIR": tmp_path / "runtime",
    }
    settings["COMPOSE_APP_DECLARATIONS"] = "angee.conf.ManifestDeclarations"
    Composer(settings).compose_settings()
    installed = _installed_paths(settings["INSTALLED_APPS"])

    assert installed.index("project_beta.apps.TestConfig") < installed.index("alpha.apps.TestConfig")


def test_composer_rejects_dependency_cycles(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Dependency cycles fail during settings composition."""

    _write_addon(tmp_path, "alpha", depends_on=("beta",))
    _write_addon(tmp_path, "beta", depends_on=("alpha",))
    monkeypatch.syspath_prepend(str(tmp_path))

    settings = {
        "INSTALLED_APPS": ("alpha",),
        "COMPOSE_RUNTIME_DIR": tmp_path / "runtime",
    }
    with pytest.raises(ImproperlyConfigured, match="Cycle"):
        settings["COMPOSE_APP_DECLARATIONS"] = "angee.conf.ManifestDeclarations"
        Composer(settings).compose_settings()


def test_addon_contribution_ignores_plain_django_dependency_urls() -> None:
    """Only an Angee addon — one carrying a manifest — opts into route mounting.

    ``django.contrib.auth`` ships a real ``urls`` submodule with ``urlpatterns``, yet
    contributes nothing: it has no ``addon.toml`` manifest, so the contract's presence
    (not a populated module, and not any dependency declaration) is the gate.
    """

    import django.contrib.auth as auth_module
    from django.contrib.auth.apps import AuthConfig

    from angee.addons import addon_contribution

    auth_config = AuthConfig("django.contrib.auth", auth_module)

    assert addon_contribution(auth_config, "urls", "urlpatterns") == []


def test_addon_contribution_loads_callable_conventional_modules(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The shared addon loader owns callable ASGI contributions."""

    _write_addon(tmp_path, "alpha")
    (tmp_path / "alpha" / "asgi.py").write_text(
        "def websocket_urlpatterns():\n    return ['ws']\n\nhttp_mounts = [('/mcp', 'app')]\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    from angee.addons import addon_contribution

    app_config = _addon_test_config("alpha")

    assert addon_contribution(app_config, "asgi", "websocket_urlpatterns", allow_callable=True) == ["ws"]
    assert addon_contribution(app_config, "asgi", "http_mounts", allow_callable=True) == [("/mcp", "app")]


def test_addon_contribution_ignores_absent_conventional_module(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """No conventional submodule means no contribution and no import."""

    _write_addon(tmp_path, "alpha_absent")
    monkeypatch.syspath_prepend(str(tmp_path))

    from angee.addons import addon_contribution

    app_config = _addon_test_config("alpha_absent")

    assert addon_contribution(app_config, "asgi", "websocket_urlpatterns", allow_callable=True) == []
    assert "alpha_absent.asgi" not in sys.modules


def test_capability_owners_ignore_unbound_conventional_annotations(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Conventional seams are declarations, not type-only annotations."""

    _write_addon(tmp_path, "alpha_annotations")
    (tmp_path / "alpha_annotations" / "schema.py").write_text(
        "schemas: dict[str, object]\n",
        encoding="utf-8",
    )
    (tmp_path / "alpha_annotations" / "mcp_tools.py").write_text(
        "register: object\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    from angee.addons import addon_manifest
    from angee.graphql.schema import schema_parts_for
    from angee.mcp.server import tool_registrar

    config = _addon_test_config("alpha_annotations")
    assert addon_manifest(config).schemas is None
    assert schema_parts_for(config) == {}
    assert tool_registrar(config) is None


def test_addon_contribution_wraps_conventional_module_import_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Import errors from conventional modules keep the old failure shape."""

    _write_addon(tmp_path, "alpha_bad_import")
    (tmp_path / "alpha_bad_import" / "asgi.py").write_text(
        "import missing_angee_test_dependency\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    from angee.addons import addon_contribution

    app_config = _addon_test_config("alpha_bad_import")

    with pytest.raises(ImproperlyConfigured, match="alpha_bad_import.asgi failed to import"):
        addon_contribution(app_config, "asgi", "websocket_urlpatterns", allow_callable=True)


def test_addon_contribution_rejects_non_iterables(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Static and callable contributions must resolve to iterables."""

    _write_addon(tmp_path, "alpha_non_iterable")
    (tmp_path / "alpha_non_iterable" / "asgi.py").write_text(
        "websocket_urlpatterns = 1\ndef http_mounts():\n    return 1\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    from angee.addons import addon_contribution

    app_config = _addon_test_config("alpha_non_iterable")

    with pytest.raises(ImproperlyConfigured, match="websocket_urlpatterns must be iterable or callable"):
        addon_contribution(app_config, "asgi", "websocket_urlpatterns", allow_callable=True)
    with pytest.raises(ImproperlyConfigured, match="http_mounts must be iterable or callable"):
        addon_contribution(app_config, "asgi", "http_mounts", allow_callable=True)


def test_addon_contribution_rejects_urlpattern_callables(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """URLConf ``urlpatterns`` stays an iterable attribute, not a callable."""

    _write_addon(tmp_path, "alpha_callable_urls")
    (tmp_path / "alpha_callable_urls" / "urls.py").write_text(
        "def urlpatterns():\n    return []\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    from angee.addons import addon_contribution

    app_config = _addon_test_config("alpha_callable_urls")

    with pytest.raises(ImproperlyConfigured, match="alpha_callable_urls.urls.urlpatterns must be iterable"):
        addon_contribution(app_config, "urls", "urlpatterns")


def test_plain_addon_autoconfig_keys_are_defaults(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Plain autoconfig keys do not override existing project settings."""

    _write_addon(
        tmp_path,
        "alpha",
        autoconfig="SETTINGS = {'ALPHA_SETTING': 'addon'}\n",
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    settings = {
        "INSTALLED_APPS": ("alpha",),
        "COMPOSE_RUNTIME_DIR": tmp_path / "runtime",
        "ALPHA_SETTING": "project",
    }
    settings["COMPOSE_APP_DECLARATIONS"] = "angee.conf.ManifestDeclarations"
    Composer(settings).compose_settings()

    assert settings["ALPHA_SETTING"] == "project"


def test_yamlconf_project_values_beat_plain_addon_defaults(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """YAML/env project values win over addon plain-key defaults."""

    manage_py = tmp_path / "manage.py"
    manage_py.write_text("# test entrypoint\n", encoding="utf-8")
    _write_addon(
        tmp_path / "addons",
        "alpha",
        autoconfig="SETTINGS = {'ALPHA_SETTING': 'addon'}\n",
    )
    (tmp_path / "settings.yaml").write_text(
        "\n".join(
            [
                "SECRET_KEY: yaml-secret",
                "ALPHA_SETTING: yaml",
                "INSTALLED_APPS:",
                "  - alpha",
                'COMPOSE_RUNTIME_DIR: "{BASE_DIR}/runtime"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delitem(sys.modules, "settings", raising=False)
    monkeypatch.setattr(sys, "argv", [str(manage_py)])
    monkeypatch.setenv("YAMLCONF_ALPHA_SETTING", "env")

    compose_settings = _load_project_settings_artifact()

    assert compose_settings.ALPHA_SETTING == "env"


def test_angee_env_values_overlay_declared_addon_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Stack env can feed declared addon settings without app code reading env."""

    manage_py = tmp_path / "manage.py"
    manage_py.write_text("# test entrypoint\n", encoding="utf-8")
    _write_addon(
        tmp_path / "addons",
        "alpha",
        autoconfig="SETTINGS = {'ANGEE_ALPHA_SECRET': 'addon-default'}\n",
    )
    (tmp_path / "settings.yaml").write_text(
        "\n".join(
            [
                "SECRET_KEY: yaml-secret",
                "ANGEE_ALPHA_SECRET: yaml-secret",
                "INSTALLED_APPS:",
                "  - alpha",
                'COMPOSE_RUNTIME_DIR: "{BASE_DIR}/runtime"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delitem(sys.modules, "settings", raising=False)
    monkeypatch.setattr(sys, "argv", [str(manage_py)])
    monkeypatch.setenv("ANGEE_ALPHA_SECRET", "env-secret")

    compose_settings = _load_project_settings_artifact()

    assert compose_settings.ANGEE_ALPHA_SECRET == "env-secret"


def test_addon_autoconfig_rejects_composer_owned_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Addon autoconfig cannot mutate settings owned by the composer."""

    _write_addon(
        tmp_path,
        "alpha",
        autoconfig="SETTINGS = {'INSTALLED_APPS': ()}\n",
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    settings = {
        "INSTALLED_APPS": ("alpha",),
        "COMPOSE_RUNTIME_DIR": tmp_path / "runtime",
    }
    with pytest.raises(ImproperlyConfigured, match="must not define INSTALLED_APPS"):
        settings["COMPOSE_APP_DECLARATIONS"] = "angee.conf.ManifestDeclarations"
        Composer(settings).compose_settings()


def test_addon_autoconfig_can_set_graphql_ide(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Addon autoconfig may own addon settings like ANGEE_GRAPHQL_IDE."""

    _write_addon(
        tmp_path,
        "alpha",
        autoconfig="SETTINGS = {'ANGEE_GRAPHQL_IDE': 'custom'}\n",
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    settings = {
        "INSTALLED_APPS": ("alpha",),
        "COMPOSE_RUNTIME_DIR": tmp_path / "runtime",
    }
    settings["COMPOSE_APP_DECLARATIONS"] = "angee.conf.ManifestDeclarations"
    Composer(settings).compose_settings()

    assert settings["ANGEE_GRAPHQL_IDE"] == "custom"


def test_autoconfig_reuses_native_module_and_preserves_incremental_values(tmp_path, monkeypatch):
    """Each later addon sees prior defaults and yamlconf tracks both overlays."""

    import django_yamlconf

    _write_addon(tmp_path, "pipeline_first", autoconfig="SETTINGS = {'ITEMS:append': ['first']}\n")
    _write_addon(
        tmp_path,
        "pipeline_second",
        autoconfig=(
            "def settings(namespace):\n"
            "    assert namespace['ITEMS'] == ['project', 'first']\n"
            "    return {'ITEMS:append': ['second']}\n"
        ),
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    modules = []
    native_add = django_yamlconf.add_attributes

    def record(settings_module, attributes, source):
        modules.append(settings_module)
        return native_add(settings_module, attributes, source)

    monkeypatch.setattr(django_yamlconf, "add_attributes", record)
    namespace = {"ITEMS": ["project"]}
    config = AutoConfig(namespace)
    config.update_app(AppConfig.create("pipeline_first"))
    config.update_app(AppConfig.create("pipeline_second"))
    assert namespace["ITEMS"] == ["project", "first", "second"]
    assert len(modules) == 2 and modules[0] is modules[1]
    assert namespace["_YAMLCONF_ATTRIBUTES"]


def test_autoconfig_nested_contributions_do_not_mutate_addon_defaults(tmp_path, monkeypatch):
    """A composed registry belongs to its host; imported declarations remain reusable."""

    _write_addon(tmp_path, "registry_owner", autoconfig="SETTINGS = {'REGISTRY': {'core': 'core.Impl'}}\n")
    _write_addon(tmp_path, "registry_extension", autoconfig="SETTINGS = {'REGISTRY.extension': 'extra.Impl'}\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    first = {}
    composer = AutoConfig(first)
    composer.update_app(AppConfig.create("registry_owner"))
    composer.update_app(AppConfig.create("registry_extension"))
    second = {}
    AutoConfig(second).update_app(AppConfig.create("registry_owner"))

    assert first["REGISTRY"] == {"core": "core.Impl", "extension": "extra.Impl"}
    assert second["REGISTRY"] == {"core": "core.Impl"}


@pytest.mark.parametrize("entry_kind", ["module", "config_path", "config_instance"])
def test_autoconfig_applies_installed_entries_without_populating_apps(tmp_path, monkeypatch, entry_kind):
    """Bare settings use native app identity and ordered merges without runtime loading."""

    _write_addon(
        tmp_path,
        "bare_first",
        depends_on=("not_installed",),
        autoconfig="SETTINGS = {'VALUE': 'default', 'ITEMS:append': ['first'], 'REGISTRY': {'first': 'first.Impl'}}\n",
    )
    _write_addon(
        tmp_path,
        "bare_second",
        autoconfig=(
            "def settings(namespace):\n"
            "    assert namespace['ITEMS'] == ['project', 'first']\n"
            "    return {'ITEMS:append': ['second'], 'REGISTRY.second': 'second.Impl'}\n"
        ),
    )
    (tmp_path / "bare_first" / "models.py").write_text("raise AssertionError('models imported')\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    first_config = AppConfig.create("bare_first")

    def forbidden_ready():
        pytest.fail("Applying autoconfig must not populate Django's app registry")

    monkeypatch.setattr(type(first_config), "ready", lambda self: forbidden_ready())
    first_entry = {
        "module": "bare_first",
        "config_path": "bare_first.apps.TestConfig",
        "config_instance": first_config,
    }[entry_kind]
    installed = [first_entry, "bare_second"]
    namespace = {"INSTALLED_APPS": installed, "VALUE": "project", "ITEMS": ["project"]}

    AutoConfig.apply_installed(namespace)

    assert namespace["INSTALLED_APPS"] is installed
    assert namespace["VALUE"] == "project"
    assert namespace["ITEMS"] == ["project", "first", "second"]
    assert namespace["REGISTRY"] == {"first": "first.Impl", "second": "second.Impl"}
    assert "bare_first.models" not in sys.modules
    assert "COMPOSE_RUNTIME_DIR" not in namespace


@pytest.mark.parametrize("name", ["INSTALLED_APPS", "COMPOSE_RUNTIME_DIR", "COMPOSE_RUNTIME_MODULE"])
def test_autoconfig_installed_apps_cannot_change_composer_settings(tmp_path, monkeypatch, name):
    """Bare and composed settings share the same reserved declaration policy."""

    _write_addon(tmp_path, "bare_mutator", autoconfig=f"SETTINGS = {{'{name}': 'unexpected'}}\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    with pytest.raises(ImproperlyConfigured, match=f"must not define {name}"):
        AutoConfig.apply_installed({"INSTALLED_APPS": ["bare_mutator"]})


def test_autoconfig_installed_apps_preserve_broken_module_imports(tmp_path, monkeypatch):
    """An existing autoconfig module's missing dependency must fail settings loading."""

    _write_addon(tmp_path, "bare_broken", autoconfig="import missing_autoconfig_dependency\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    with pytest.raises(ModuleNotFoundError, match="missing_autoconfig_dependency"):
        AutoConfig.apply_installed({"INSTALLED_APPS": ["bare_broken"]})


def test_bare_settings_use_addon_owned_defaults(settings):
    """Source tests share production policy and provider defaults with fixture additions."""

    assert settings.REBAC_LOCAL_BACKEND_STORAGE == "registry"
    assert settings.REBAC_STRICT_MODE is True
    assert settings.CELERY_WORKER_PREFETCH_MULTIPLIER == 1
    steps = settings.ANGEE_WORKFLOW_STEP_CLASSES
    assert steps["ask_decision"] == "angee.workflows.decision_steps.AskDecision"
    assert steps["map"] == "angee.workflows.maps.Map"
    assert steps["await_run"] == "angee.workflows.awaits.AwaitRun"
    assert settings.ANGEE_WORKFLOW_MAP_CONCURRENCY == 10
    assert settings.ANGEE_WORKFLOW_RETENTION_DAYS == 90
    assert {
        "prepare_pages",
        "recognize_page",
        "process_evidence",
        "infer_evidence",
    } <= steps.keys()
    assert {"none", "retention_notes", "step_text"} <= settings.ANGEE_EXTRACTION_PROFILE_CLASSES.keys()
    assert not hasattr(settings, "ANGEE_EXTRACTION_BACKEND_CLASSES")
    assert {"email", "webform", "manual", "fake_live"} <= settings.ANGEE_CHANNEL_BACKEND_CLASSES.keys()
    assert {"anthropic", "openai", "ollama", "stub_inference"} <= settings.ANGEE_INFERENCE_BACKEND_CLASSES.keys()
    assert "pydantic" in settings.ANGEE_AGENT_RUNTIME_CLASSES
    assert "stub" in settings.ANGEE_VCS_BACKEND_CLASSES
    assert "stub" in settings.ANGEE_POSTS_FEED_BACKEND_CLASSES


def test_bare_autoconfig_ignores_all_environment_sources(monkeypatch):
    """Bare settings cannot pick up shell broker, channel, mail or operator services."""

    for name, value in {
        "REDIS_URL": "redis://cache.example.test/0",
        "CHANNEL_REDIS_URL": "redis://channels.example.test/1",
        "CELERY_BROKER_URL": "redis://broker.example.test/2",
        "ANYMAIL": "not valid JSON",
        "ANYMAIL_MAILGUN_API_KEY": "shell-value",
        "ANGEE_OPERATOR_GRAPHQL_ENDPOINT": "https://operator.example.test/graphql",
        "ANGEE_OPERATOR_URL": "https://operator.example.test",
        "ANGEE_OPERATOR_TOKEN": "shell-token",
    }.items():
        monkeypatch.setenv(name, value)
    namespace = {"INSTALLED_APPS": ["angee.jobs", "angee.graphql", "angee.messaging", "angee.operator"]}

    AutoConfig.apply_installed(namespace, environment=False)

    assert namespace["CHANNEL_LAYERS"] == {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
    assert "CELERY_BROKER_URL" not in namespace
    assert "ANYMAIL" not in namespace
    assert "ANYMAIL_MAILGUN_API_KEY" not in namespace
    assert namespace["ANGEE_OPERATOR_GRAPHQL_ENDPOINT"] == "/operator/graphql"
    assert namespace["ANGEE_OPERATOR_URL"] is None
    assert namespace["ANGEE_OPERATOR_TOKEN"] is None


@pytest.mark.parametrize("include_messaging", [False, True])
def test_mail_provider_environment_belongs_to_messaging(tmp_path, monkeypatch, include_messaging):
    """Installed messaging derives provider values with the shared environment precedence."""

    monkeypatch.setenv("ANYMAIL", '{"MAILGUN_API_KEY": "environment"}')
    monkeypatch.setenv("ANYMAIL_MAILGUN_SENDER_DOMAIN", "example.test")
    namespace = {
        "INSTALLED_APPS": ("angee.messaging",) if include_messaging else (),
        "COMPOSE_RUNTIME_DIR": tmp_path / "runtime",
        "EMAIL_BACKEND": "anymail.backends.mailgun.EmailBackend",
        "ANYMAIL": {"MAILGUN_API_KEY": "project"},
    }
    namespace["COMPOSE_APP_DECLARATIONS"] = "angee.conf.ManifestDeclarations"
    Composer(namespace).compose_settings()
    assert namespace["ANYMAIL"] == {"MAILGUN_API_KEY": "environment" if include_messaging else "project"}
    if include_messaging:
        assert namespace["ANYMAIL_MAILGUN_SENDER_DOMAIN"] == "example.test"
        assert namespace["ANGEE_EMAIL_DELIVERY_CONFIGURED"] is True
    else:
        assert "ANYMAIL_MAILGUN_SENDER_DOMAIN" not in namespace


def test_plain_composer_preserves_host_choices_and_adds_no_capability_defaults(tmp_path):
    namespace = {
        "INSTALLED_APPS": ("tests.settings.BareComposeConfig",),
        "COMPOSE_RUNTIME_DIR": tmp_path / "runtime",
        "ROOT_URLCONF": "project.urls",
        "ASGI_APPLICATION": "project.asgi.application",
    }
    Composer(namespace).compose_settings()
    assert [config.name for config in namespace["INSTALLED_APPS"]] == ["angee.compose"]
    assert namespace["ROOT_URLCONF"] == "project.urls"
    assert namespace["ASGI_APPLICATION"] == "project.asgi.application"
    assert "MIDDLEWARE" not in namespace
    assert "COMPOSE_INTEGRATIONS" not in namespace
    assert "REBAC_BACKEND" not in namespace
    assert "ANGEE_CHANGESET_MODEL" not in namespace


def test_host_only_autoconfig_does_not_install_foundation_or_job_policy() -> None:
    """Native host hooks do not make optional model and execution settings mandatory."""
    namespace = {"INSTALLED_APPS": ["angee"]}
    AutoConfig.apply_installed(namespace, environment=False)
    assert namespace["INSTALLED_APPS"] == ["angee"]
    for name in ("ANGEE_IMPL_REGISTRIES", "ANGEE_MERGE_GUARDS", "REBAC_STRICT_MODE",
                 "ANGEE_CHANGESET_MODEL", "CELERY_TASK_SOFT_TIME_LIMIT"):
        assert name not in namespace


def test_packaged_authored_settings_and_deployment_overrides_share_native_yamlconf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deployment secrets need no repeated roots; SOURCE_DIR differs from BASE_DIR."""

    artifact = tmp_path / "artifact"
    deployment = tmp_path / "deployment"
    artifact.mkdir()
    deployment.mkdir()
    raw_yaml = artifact / "runtime" / "settings.yaml"
    raw_yaml.parent.mkdir()
    raw_yaml.write_text(
        "INSTALLED_APPS: [angee.resources]\n"
        "ANGEE_ADDON_DIRS: ['{SOURCE_DIR}']\n"
        "CUSTOM_CONFIG: {path: '{SOURCE_DIR}/assets', value: authored}\n"
        "SECRET_KEY: authored-placeholder\n",
    )
    (deployment / "settings.yaml").write_text(
        "SECRET_KEY: deployment-secret\n'CUSTOM_CONFIG:append': {value: deployed}\n"
    )
    namespace: dict[str, Any] = {}
    ProjectContract(namespace).load(root=deployment, authored_root=artifact, authored_yaml=raw_yaml)

    assert namespace["SECRET_KEY"] == "deployment-secret"
    assert namespace["BASE_DIR"] == deployment
    assert namespace["ANGEE_ADDON_DIRS"] == (artifact,)
    assert namespace["CUSTOM_CONFIG"] == {"path": str(artifact / "assets"), "value": "deployed"}
    assert "angee.resources" in namespace["INSTALLED_APPS"]
    assert "SOURCE_DIR" not in namespace


def test_packaged_python_declarations_resolve_helpers_from_the_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deployment cwd cannot replace an explicitly packaged declaration module."""

    artifact = tmp_path / "artifact"
    deployment = tmp_path / "deployment"
    artifact.mkdir()
    deployment.mkdir()
    (artifact / "settings.yaml").write_text("{}\n")
    (artifact / "artifact_helper.py").write_text("CUSTOM_SETTING = 'packaged-helper'\n")
    (artifact / "artifact_settings.py").write_text(
        "from artifact_helper import CUSTOM_SETTING\nINSTALLED_APPS = ['angee.resources']\n",
    )
    (deployment / "artifact_settings.py").write_text("raise AssertionError('deployment shadow imported')\n")
    monkeypatch.syspath_prepend(str(deployment))
    for module in ("artifact_settings", "artifact_helper"):
        monkeypatch.delitem(sys.modules, module, raising=False)
    namespace: dict[str, Any] = {}
    try:
        ProjectContract(namespace).load(
            root=deployment, authored_root=artifact, authored_module="artifact_settings",
            authored_yaml=artifact / "settings.yaml",
        )
        assert namespace["CUSTOM_SETTING"] == "packaged-helper"
        assert namespace["BASE_DIR"] == deployment
        assert "angee.resources" in namespace["INSTALLED_APPS"]
    finally:
        for module in ("artifact_settings", "artifact_helper"):
            sys.modules.pop(module, None)


def test_editable_settings_apply_the_live_yaml_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The editable snapshot cannot duplicate or replace the live YAML declaration."""

    (tmp_path / "editable_settings.py").write_text("CUSTOM_LIST = ['base']\n")
    (tmp_path / "settings.yaml").write_text("'CUSTOM_LIST:append': [live]\n")
    resource = tmp_path / "runtime" / "settings.yaml"
    resource.parent.mkdir()
    resource.write_text("'CUSTOM_LIST:append': [stale-snapshot]\n")
    monkeypatch.delitem(sys.modules, "editable_settings", raising=False)
    namespace: dict[str, Any] = {}
    try:
        ProjectContract(namespace).load(
            root=tmp_path, authored_root=tmp_path, authored_module="editable_settings", authored_yaml=resource,
        )
        assert namespace["CUSTOM_LIST"] == ["base", "live"]
    finally:
        sys.modules.pop("editable_settings", None)


def test_yaml_only_editable_settings_accept_native_initial_source_without_importing_a_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Native provenance names the empty declaration namespace before YAML wins."""

    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.delitem(sys.modules, "settings", raising=False)
    (tmp_path / "settings.yaml").write_text(
        "SECRET_KEY: yaml-only\nINSTALLED_APPS: []\n'CUSTOM_LIST:append': [live]\n",
    )
    resource = tmp_path / "runtime" / "settings.yaml"
    resource.parent.mkdir()
    resource.write_text("'CUSTOM_LIST:append': [stale-snapshot]\n")
    namespace: dict[str, Any] = {}
    ProjectContract(namespace).load(
        root=tmp_path, authored_root=tmp_path, authored_module=None, authored_yaml=resource,
    )

    assert namespace["SECRET_KEY"] == "yaml-only"
    assert namespace["CUSTOM_LIST"] == ["live"]
    effective = ModuleType("effective_settings")
    vars(effective).update(namespace)
    info = django_yamlconf.get_attr_info("SECRET_KEY", effective)
    assert info is not None
    assert info["source"] == str(tmp_path / "settings.yaml")
    assert (None, "settings") in info["history"]
    assert "settings" not in sys.modules
