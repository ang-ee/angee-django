"""Angee settings policy composes the native package and configuration owners."""
from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import pytest
from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured

from angee.base.conf import configure
from angee.base.testing.packages import write_addon_distribution

BASE_APP = "angee.base"
GRAPHQL_APP = "angee.graphql"
RESOURCES_APP = "angee.resources"
COMPOSE_APP = "django_compose.apps.BuildComposeConfig"


@pytest.fixture(autouse=True)
def native_write_inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DJANGO_COMPOSE_MODE", "write")
    monkeypatch.setenv("ANGEE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("YAMLCONF_CONFFILE", raising=False)


def _configure(namespace: dict[str, Any], directory: Path) -> None:
    namespace.setdefault("__file__", str(directory / "settings.py"))
    namespace.setdefault("COMPOSE_OUTPUT_DIR", directory / "runtime")
    configure(namespace)


def _compose(directory: Path) -> dict[str, Any]:
    namespace: dict[str, Any] = {"INSTALLED_APPS": ("example.notes",)}
    _configure(namespace, directory)
    return namespace


def _write_addon(
    root: Path, name: str, *, depends_on: tuple[str, ...] = (), autoconfig: str = "SETTINGS = {}\n",
) -> None:
    package = root / name
    package.mkdir()
    (package / "__init__.py").write_text("")
    write_addon_distribution(package, name, depends_on=depends_on)
    (package / "autoconfig.py").write_text(autoconfig)
    (package / "apps.py").write_text(
        "from django.apps import AppConfig\nclass TestConfig(AppConfig):\n"
        f"    name = {name!r}\n",
    )


def test_base_is_installed_exactly_once(tmp_path: Path) -> None:
    """The model foundation is a normal installed Django app."""

    settings = _compose(tmp_path)
    installed = settings["INSTALLED_APPS"]
    assert installed.count(BASE_APP) == 1



def test_resources_root_expands_framework_dependencies(tmp_path: Path) -> None:
    """A host can name resources alone and get its framework boot closure."""

    settings: dict[str, Any] = {
        "INSTALLED_APPS": ("angee.resources",),
        "COMPOSE_OUTPUT_DIR": tmp_path / "runtime",
    }
    _configure(settings, tmp_path)
    installed = settings["INSTALLED_APPS"]

    compose_at = installed.index(COMPOSE_APP)
    base_at = installed.index(BASE_APP)
    resources_at = installed.index(RESOURCES_APP)

    assert base_at < resources_at < compose_at
    assert GRAPHQL_APP not in installed
    assert "angee.iam" not in installed
    assert "REBAC_UNIVERSAL_ADMIN_ROLE" not in settings
    assert "angee.jobs" not in installed
    assert "simple_history" not in installed
    assert "angee.provenance" not in installed
    assert "CELERY_TASK_SOFT_TIME_LIMIT" not in settings
    assert "ANGEE_CHANGESET_MODEL" not in settings



def test_iam_user_is_the_default_auth_model(tmp_path: Path) -> None:
    """Composed hosts use Angee's swappable IAM user."""

    settings = _compose(tmp_path)
    installed = settings["INSTALLED_APPS"]

    assert COMPOSE_APP in installed
    assert BASE_APP in installed
    assert GRAPHQL_APP in installed
    assert RESOURCES_APP in installed
    assert settings["AUTH_USER_MODEL"] == "iam.User"
    assert "angee.iam" in installed



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
        "INSTALLED_APPS": ("angee.iam",),
        "COMPOSE_OUTPUT_DIR": tmp_path / "runtime",
        **overrides,
    }
    _configure(settings, tmp_path)

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
    installed = settings["INSTALLED_APPS"]

    assert "axes" in installed
    assert "axes.middleware.AxesMiddleware" in settings["MIDDLEWARE"]
    assert SETTINGS["AUTHENTICATION_BACKENDS:append"] == [
        "axes.backends.AxesStandaloneBackend",
        "angee.iam.auth.ModelBackend",
    ]
    assert settings["AUTHENTICATION_BACKENDS"][0] == "axes.backends.AxesStandaloneBackend"
    assert "rebac.W001" in settings["SILENCED_SYSTEM_CHECKS"]



def test_addons_are_sorted_by_declared_dependencies(tmp_path: Path) -> None:
    """The host may list addons in any order; Their native requirements order them."""

    settings = _compose(tmp_path)
    installed = settings["INSTALLED_APPS"]

    compose_at = installed.index(COMPOSE_APP)
    base_at = installed.index(BASE_APP)
    graphql_at = installed.index(GRAPHQL_APP)
    iam_at = installed.index("angee.iam")
    resources_at = installed.index(RESOURCES_APP)
    notes_at = installed.index("example.notes")

    assert base_at < resources_at < compose_at
    assert base_at < graphql_at < iam_at
    assert resources_at < iam_at
    assert iam_at < notes_at



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
        "COMPOSE_OUTPUT_DIR": tmp_path / "runtime",
        "REBAC_LINT_BARE_PREFETCH": True,
    }
    _configure(settings, tmp_path)

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
        "COMPOSE_OUTPUT_DIR": tmp_path / "debug-runtime",
        "DEBUG": True,
    }
    _configure(debug_settings, tmp_path)

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



def _addon_test_config(name: str) -> AppConfig:
    """Return an ``AppConfig`` for a temp addon package.

    The temp package's native distribution entry point declares addon identity;
    requires_apps on its config supplies foreign Django dependencies.
    """

    module = importlib.import_module(name)
    config_cls = getattr(importlib.import_module(f"{name}.apps"), "TestConfig")
    return config_cls(name, module)



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

    from angee.base.addons import addon_facts
    from angee.graphql.schema import schema_parts_for
    from angee.mcp.server import tool_registrar

    config = _addon_test_config("alpha_annotations")
    assert addon_facts(config.name, "angee.graphql") == {}
    assert schema_parts_for(config) == {}
    assert tool_registrar(config) is None



def test_bare_settings_use_addon_owned_defaults(settings, tmp_path: Path):
    """Source tests share production policy and provider defaults with fixture additions."""

    assert settings.REBAC_LOCAL_BACKEND_STORAGE == "registry"
    assert settings.REBAC_STRICT_MODE is True
    production = {"INSTALLED_APPS": ("example.notes", "angee.workflows_extraction")}
    _configure(production, tmp_path)
    assert production["CELERY_WORKER_PREFETCH_MULTIPLIER"] == 1
    steps = settings.ANGEE_WORKFLOW_STEP_CLASSES
    assert steps["ask_decision"] == "angee.workflows.decision_steps.AskDecision"
    assert steps["map"] == "angee.workflows.maps.Map"
    assert steps["await_run"] == "angee.workflows.awaits.AwaitRun"
    assert production["ANGEE_WORKFLOW_MAP_CONCURRENCY"] == 10
    assert production["ANGEE_WORKFLOW_RETENTION_DAYS"] == 90
    assert {
        "prepare_pages",
        "recognize_page",
        "process_evidence",
        "infer_evidence",
    } <= production["ANGEE_WORKFLOW_STEP_CLASSES"].keys()
    assert {"none", "retention_notes", "step_text"} <= settings.ANGEE_EXTRACTION_PROFILE_CLASSES.keys()
    assert not hasattr(settings, "ANGEE_EXTRACTION_BACKEND_CLASSES")
    assert {"email", "webform", "manual", "fake_live"} <= settings.ANGEE_CHANNEL_BACKEND_CLASSES.keys()
    assert {"anthropic", "openai", "ollama", "stub_inference"} <= settings.ANGEE_INFERENCE_BACKEND_CLASSES.keys()
    assert "pydantic" in settings.ANGEE_AGENT_RUNTIME_CLASSES
    assert "stub" in settings.ANGEE_VCS_BACKEND_CLASSES
    assert "stub" in settings.ANGEE_POSTS_FEED_BACKEND_CLASSES



@pytest.mark.parametrize("include_messaging", [False, True])
def test_mail_provider_environment_belongs_to_messaging(tmp_path, monkeypatch, include_messaging):
    """Installed messaging derives provider values with the shared environment precedence."""

    (tmp_path / "settings.yaml").write_text("ANYMAIL:jsonenv: true\n")
    monkeypatch.setenv("YAMLCONF_ANYMAIL", '{"MAILGUN_API_KEY": "environment"}')
    monkeypatch.setenv("YAMLCONF_ANYMAIL_MAILGUN_SENDER_DOMAIN", "example.test")
    namespace = {
        "INSTALLED_APPS": ("angee.messaging",) if include_messaging else (),
        "COMPOSE_OUTPUT_DIR": tmp_path / "runtime",
        "MAILERS": {"default": {"BACKEND": "anymail.backends.mailgun.EmailBackend"}},
        "ANYMAIL": {"MAILGUN_API_KEY": "project"},
    }
    _configure(namespace, tmp_path)
    assert namespace["ANYMAIL"] == {"MAILGUN_API_KEY": "environment"}
    if include_messaging:
        assert namespace["ANYMAIL_MAILGUN_SENDER_DOMAIN"] == "example.test"
        assert namespace["ANGEE_EMAIL_DELIVERY_CONFIGURED"] is True
    else:
        assert "ANGEE_EMAIL_DELIVERY_CONFIGURED" not in namespace
    assert namespace["ANYMAIL_MAILGUN_SENDER_DOMAIN"] == "example.test"


def test_public_origin_joins_csrf_trusted_origins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANGEE_PUBLIC_ORIGIN", "https://demo.example.com")
    namespace = {"INSTALLED_APPS": []}
    _configure(namespace, tmp_path)
    assert namespace["CSRF_TRUSTED_ORIGINS"] == ["https://demo.example.com"]


@pytest.mark.parametrize(("project", "environment", "expected"), [(None, None, 60), (30, None, 30), (30, "15", 15)])
def test_server_statement_timeout_preserves_project_and_environment_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, project: int | None, environment: str | None, expected: int,
) -> None:
    monkeypatch.delenv("ANGEE_WEB_STATEMENT_TIMEOUT", raising=False)
    if environment is not None:
        monkeypatch.setenv("ANGEE_WEB_STATEMENT_TIMEOUT", environment)
    namespace = {"INSTALLED_APPS": ["angee.server"]}
    if project is not None:
        namespace["ANGEE_WEB_STATEMENT_TIMEOUT"] = project
    _configure(namespace, tmp_path)
    assert float(namespace["ANGEE_WEB_STATEMENT_TIMEOUT"]) == expected


def test_data_directory_belongs_to_base_defaults(tmp_path: Path) -> None:
    namespace = {"INSTALLED_APPS": ["angee.base"]}
    _configure(namespace, tmp_path)
    assert namespace["ANGEE_DATA_DIR"] == tmp_path / ".angee/data"
    assert namespace["DATABASES"]["default"]["NAME"] == tmp_path / ".angee/data/db.sqlite3"
    assert namespace["STATIC_ROOT"] == tmp_path / ".angee/data/static"
    assert namespace["MEDIA_ROOT"] == tmp_path / ".angee/data/media"


def test_graphql_ide_rejects_competing_defaults_and_preserves_authored_choice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_addon(
        tmp_path, "custom_ide", depends_on=("angee.graphql",),
        autoconfig="SETTINGS = {'ANGEE_GRAPHQL_IDE': 'custom'}\n",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    with pytest.raises(ImproperlyConfigured, match="ANGEE_GRAPHQL_IDE has competing scalar defaults"):
        _configure({"INSTALLED_APPS": ["custom_ide"]}, tmp_path)
    namespace = {"INSTALLED_APPS": ["angee.graphql"], "ANGEE_GRAPHQL_IDE": "custom"}
    _configure(namespace, tmp_path)
    assert namespace["ANGEE_GRAPHQL_IDE"] == "custom"


@pytest.mark.parametrize("environment", [False, True])
def test_native_environment_values_reach_declared_and_derived_capability_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, environment: bool,
) -> None:
    (tmp_path / "settings.yaml").write_text("ANYMAIL:jsonenv: true\n")
    values = {
        "YAMLCONF_CELERY_BROKER_URL": "redis://broker.example.test/1",
        "YAMLCONF_CHANNEL_REDIS_URL": "redis://channels.example.test/2",
        "ANGEE_OPERATOR_URL": "https://operator.example.test",
        "YAMLCONF_ANYMAIL": '{"MAILGUN_API_KEY": "environment"}',
    }
    for name, value in values.items():
        monkeypatch.delenv(name, raising=False)
        if environment:
            monkeypatch.setenv(name, value)
    namespace = {
        "INSTALLED_APPS": ["angee.jobs", "angee.graphql", "angee.operator", "angee.messaging"],
        "CELERY_BROKER_URL": "redis://project.example.test/0", "CHANNEL_REDIS_URL": "redis://project.example.test/1",
        "ANGEE_OPERATOR_URL": "https://project.example.test", "ANYMAIL": {"MAILGUN_API_KEY": "project"},
    }
    _configure(namespace, tmp_path)
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
