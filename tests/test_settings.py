"""Tests for the Angee composer settings owner."""

from __future__ import annotations

import importlib
import runpy
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured

from angee.compose.composer import Composer
from angee.project import PROJECT_DIR_ENV, find_project_dir, project_dir


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
        "INSTALLED_APPS": ("example.notes",),
        "ANGEE_RUNTIME_DIR": tmp_path / "runtime",
    }
    Composer(settings).compose_settings()
    return settings


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
    base_app = "angee.base"
    assert installed.count(base_app) == 1


def test_resources_root_expands_framework_dependencies(tmp_path: Path) -> None:
    """A host can name resources alone and get its framework boot closure."""

    settings: dict[str, Any] = {
        "INSTALLED_APPS": ("angee.resources",),
        "ANGEE_RUNTIME_DIR": tmp_path / "runtime",
    }
    Composer(settings).compose_settings()
    installed = _installed_paths(settings["INSTALLED_APPS"])

    compose_at = installed.index("angee.compose.apps.ComposeConfig")
    base_at = installed.index("angee.base")
    resources_at = installed.index("angee.resources")

    assert compose_at < base_at < resources_at
    assert "angee.graphql" not in installed
    assert "angee.iam.apps.IAMConfig" not in installed


def test_iam_user_is_the_default_auth_model(tmp_path: Path) -> None:
    """Composed hosts use Angee's swappable IAM user."""

    settings = _compose(tmp_path)
    installed = _installed_paths(settings["INSTALLED_APPS"])

    assert "angee.compose.apps.ComposeConfig" in installed
    assert "angee.base" in installed
    assert "angee.graphql" in installed
    assert "angee.resources" in installed
    assert settings["AUTH_USER_MODEL"] == "iam.User"
    assert "angee.iam.apps.IAMConfig" in installed


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


def test_iam_auth_throttling_comes_from_axes(tmp_path: Path) -> None:
    """IAM wires login throttling at Django's authenticate/backend seam."""

    from angee.iam.autoconfig import SETTINGS

    settings = _compose(tmp_path)
    installed = _installed_paths(settings["INSTALLED_APPS"])

    assert "axes.apps.AppConfig" in installed
    assert "axes.middleware.AxesMiddleware" in settings["MIDDLEWARE"]
    assert SETTINGS["AUTHENTICATION_BACKENDS:append"] == [
        "axes.backends.AxesStandaloneBackend",
        "rebac.backends.auth.RebacBackend",
        "angee.iam.auth.ModelBackend",
    ]
    assert settings["AUTHENTICATION_BACKENDS"][0] == "axes.backends.AxesStandaloneBackend"


def test_addons_are_sorted_by_declared_dependencies(tmp_path: Path) -> None:
    """The host may list addons in any order; AppConfig depends_on orders them."""

    settings = _compose(tmp_path)
    installed = _installed_paths(settings["INSTALLED_APPS"])

    compose_at = installed.index("angee.compose.apps.ComposeConfig")
    base_at = installed.index("angee.base")
    graphql_at = installed.index("angee.graphql")
    iam_at = installed.index("angee.iam.apps.IAMConfig")
    resources_at = installed.index("angee.resources")
    notes_at = installed.index("example.notes")

    assert compose_at < base_at < resources_at
    assert base_at < graphql_at < iam_at
    assert resources_at < iam_at
    assert iam_at < notes_at


def test_notes_app_order_is_stable(tmp_path: Path) -> None:
    """The notes example app order is deterministic."""

    settings = _compose(tmp_path)

    assert _installed_paths(settings["INSTALLED_APPS"]) == [
        "django_yamlconf",
        "angee.compose.apps.ComposeConfig",
        "django.contrib.contenttypes.apps.ContentTypesConfig",
        "rebac.apps.RebacConfig",
        "reversion.apps.ReversionConfig",
        "simple_history",
        "angee.base",
        "channels.apps.ChannelsConfig",
        "angee.graphql",
        "angee.resources",
        "axes.apps.AppConfig",
        "django.contrib.auth.apps.AuthConfig",
        "django.contrib.sessions.apps.SessionsConfig",
        "angee.iam.apps.IAMConfig",
        # integrate depends on angee.tasks (its periodic bridge tick), pulling the
        # task seam ahead of every integration addon.
        "angee.tasks",
        "angee.integrate.apps.IntegrateConfig",
        "angee.mcp",
        "angee.operator",
        "angee.agents.apps.AgentsConfig",
        "angee.agents_integrate_anthropic",
        "angee.iam_integrate_oidc.apps.IAMIntegrateOidcConfig",
        "angee.storage.apps.StorageConfig",
        "angee.parties.apps.PartiesConfig",
        "angee.messaging.apps.MessagingConfig",
        "angee.workflows.apps.WorkflowsConfig",
        "example.notes",
    ]


def test_one_app_set_orders_compose_before_adopters(
    tmp_path: Path,
) -> None:
    """One app set: the composer emits before base and source addon imports.

    The composer's ``import_models`` renders ``runtime/<label>`` in phase 2; it
    must run before normal app model imports continue, so it is ordered first.
    """

    settings = _compose(tmp_path)
    installed = _installed_paths(settings["INSTALLED_APPS"])

    assert installed.count("angee.compose.apps.ComposeConfig") == 1
    assert installed.count("angee.base") == 1
    assert installed.count("angee.graphql") == 1
    assert installed.count("angee.resources") == 1
    compose_at = installed.index("angee.compose.apps.ComposeConfig")
    base_at = installed.index("angee.base")
    notes_at = installed.index("example.notes")
    assert compose_at < base_at < notes_at
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
        "ANGEE_RUNTIME_DIR": tmp_path / "runtime",
        "REBAC_LINT_BARE_PREFETCH": True,
    }
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
        "rebac.backends.auth.RebacBackend",
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
        "ANGEE_RUNTIME_DIR": tmp_path / "debug-runtime",
        "DEBUG": True,
    }
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
        "CONFIG": {"hosts": ["redis://127.0.0.1:6379/0"]},
    }


def test_tasks_use_celery_broker_url_from_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Celery uses the task broker URL injected by the stack."""

    monkeypatch.setenv("CELERY_BROKER_URL", "redis://127.0.0.1:6379/1")

    settings = _compose(tmp_path)

    assert settings["CELERY_BROKER_URL"] == "redis://127.0.0.1:6379/1"
    assert settings["CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP"] is True


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
        "ANGEE_RUNTIME_DIR": runtime_dir,
    }
    Composer(settings).compose_settings()

    assert sys.path[0] == str(runtime_dir.parent)


def test_runtime_dir_string_matches_normalized_composer_path(tmp_path: Path) -> None:
    """String runtime-dir values do not conflict with Composer's normalized path."""

    runtime_dir = tmp_path / "runtime"
    settings: dict[str, Any] = {
        "INSTALLED_APPS": ("angee.resources",),
        "ANGEE_RUNTIME_DIR": str(runtime_dir),
    }
    Composer(settings).compose_settings()

    assert settings["ANGEE_RUNTIME_DIR"] == runtime_dir


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
    project.DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}  # type: ignore[attr-defined]
    project.ANGEE_DATA_DIR = tmp_path / "data"  # type: ignore[attr-defined]
    project.INSTALLED_APPS = ("angee.resources",)  # type: ignore[attr-defined]
    project.ANGEE_RUNTIME_DIR = tmp_path / "runtime"  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, project.__name__, project)
    monkeypatch.setenv("ANGEE_PROJECT_DIR", str(tmp_path))

    import angee.compose.settings as compose_settings

    compose_settings = importlib.reload(compose_settings)

    assert compose_settings.SECRET_KEY == "bridge-secret"
    assert compose_settings.DEBUG is True
    assert compose_settings.ANGEE_DATA_DIR == tmp_path / "data"
    assert compose_settings.ANGEE_RUNTIME_DIR == tmp_path / "runtime"
    assert compose_settings.ROOT_URLCONF == "angee.urls"
    assert compose_settings.ASGI_APPLICATION == "angee.asgi.application"
    installed = _installed_paths(compose_settings.INSTALLED_APPS)
    assert "angee.compose.apps.ComposeConfig" in installed
    assert "angee.resources" in installed
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
                f"ANGEE_RUNTIME_DIR = {str(tmp_path / 'runtime')!r}",
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

    import angee.compose.settings as compose_settings

    compose_settings = importlib.reload(compose_settings)

    assert compose_settings.SECRET_KEY == "package-secret"
    assert compose_settings.ANGEE_RUNTIME_DIR == tmp_path / "runtime"
    installed = _installed_paths(compose_settings.INSTALLED_APPS)
    assert "angee.resources" in installed


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
        import angee.compose.settings as compose_settings

        importlib.reload(compose_settings)

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
                f"ANGEE_RUNTIME_DIR = {str(tmp_path / 'runtime')!r}",
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

    import angee.compose.settings as compose_settings

    compose_settings = importlib.reload(compose_settings)

    assert compose_settings.SECRET_KEY == "yaml-secret"
    assert compose_settings.DEBUG is True


def test_compose_settings_module_reads_settings_yaml(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """settings.yaml can declare only composition facts and project overrides."""

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
                'ANGEE_RUNTIME_DIR: "{BASE_DIR}/generated/runtime"',
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

    import angee.compose.settings as compose_settings

    compose_settings = importlib.reload(compose_settings)

    assert compose_settings.SECRET_KEY == "yaml-secret"
    assert compose_settings.BASE_DIR == tmp_path
    assert compose_settings.ANGEE_ADDON_DIRS == (tmp_path / "addons",)
    assert compose_settings.ANGEE_RUNTIME_DIR == tmp_path / "generated" / "runtime"
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
    assert "angee.resources" in installed
    assert installed.index("angee.compose.apps.ComposeConfig") < installed.index("angee.resources")


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
                'ANGEE_RUNTIME_DIR: "{BASE_DIR}/runtime"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delitem(sys.modules, "settings", raising=False)
    monkeypatch.setattr(sys, "argv", [str(manage_py)])

    import angee.compose.settings as compose_settings

    compose_settings = importlib.reload(compose_settings)

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
                'ANGEE_RUNTIME_DIR: "{BASE_DIR}/runtime"',
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

    import angee.compose.settings as compose_settings

    compose_settings = importlib.reload(compose_settings)

    assert compose_settings.SECRET_KEY == "final-secret"


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
                'ANGEE_RUNTIME_DIR: "{BASE_DIR}/runtime"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delitem(sys.modules, "settings", raising=False)
    monkeypatch.setattr(sys, "argv", [str(manage_py)])
    monkeypatch.setenv("YAMLCONF_SECRET_KEY", "env-secret")

    import angee.compose.settings as compose_settings

    compose_settings = importlib.reload(compose_settings)

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
                'ANGEE_RUNTIME_DIR: "{BASE_DIR}/runtime"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delitem(sys.modules, "settings", raising=False)
    monkeypatch.setattr(sys, "argv", [str(manage_py)])
    monkeypatch.setenv("YAMLCONF_DEBUG", "false")
    monkeypatch.setenv("YAMLCONF_ALLOWED_HOSTS", '["example.test"]')

    import angee.compose.settings as compose_settings

    compose_settings = importlib.reload(compose_settings)

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
                'ANGEE_RUNTIME_DIR: "{BASE_DR}/runtime"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delitem(sys.modules, "settings", raising=False)
    monkeypatch.setattr(sys, "argv", [str(manage_py)])

    with pytest.raises(ImproperlyConfigured, match="Undefined attribute"):
        import angee.compose.settings as compose_settings

        importlib.reload(compose_settings)


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
        import angee.compose.settings as compose_settings

        importlib.reload(compose_settings)


def test_compose_settings_module_rejects_ancestor_settings_yaml(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Ancestor settings.yaml files must not silently override a project."""

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
                'ANGEE_RUNTIME_DIR: "{BASE_DIR}/runtime"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delitem(sys.modules, "settings", raising=False)
    monkeypatch.setattr(sys, "argv", [str(manage_py)])

    with pytest.raises(ImproperlyConfigured, match="Unexpected django-yamlconf source"):
        import angee.compose.settings as compose_settings

        importlib.reload(compose_settings)


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
                'ANGEE_RUNTIME_DIR: "{BASE_DIR}/runtime"',
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

    import angee.compose.settings as compose_settings

    compose_settings = importlib.reload(compose_settings)

    assert compose_settings.SECRET_KEY == "asgi-secret"
    assert compose_settings.BASE_DIR == tmp_path
    assert compose_settings.ANGEE_RUNTIME_DIR == tmp_path / "runtime"
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
                'ANGEE_RUNTIME_DIR: "{BASE_DIR}/runtime"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(nested)
    monkeypatch.delitem(sys.modules, "settings", raising=False)
    monkeypatch.delenv("ANGEE_PROJECT_DIR", raising=False)
    monkeypatch.setattr(sys, "argv", ["uvicorn", "angee.asgi:application"])

    import angee.compose.settings as compose_settings

    compose_settings = importlib.reload(compose_settings)

    assert compose_settings.SECRET_KEY == "nested-asgi-secret"
    assert compose_settings.BASE_DIR == project
    assert compose_settings.ANGEE_RUNTIME_DIR == project / "runtime"


def test_compose_settings_rejects_cwd_without_project_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A random working directory must not become the project root."""

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANGEE_PROJECT_DIR", raising=False)
    monkeypatch.delitem(sys.modules, "settings", raising=False)
    monkeypatch.setattr(sys, "argv", ["uvicorn", "angee.asgi:application"])

    compose_settings = sys.modules.get("angee.compose.settings")
    with pytest.raises(ImproperlyConfigured, match="ANGEE_PROJECT_DIR"):
        if compose_settings is None:
            importlib.import_module("angee.compose.settings")
        else:
            importlib.reload(compose_settings)

    assert not (tmp_path / "db.sqlite3").exists()
    assert not (tmp_path / ".angee").exists()


def test_defaults_module_requires_compose_seed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The defaults module must not derive BASE_DIR from cwd when run directly."""

    monkeypatch.chdir(tmp_path)

    with pytest.raises(ImproperlyConfigured, match="angee.compose.settings"):
        runpy.run_module("angee.compose.defaults", run_name="__test_defaults__")

    assert not (tmp_path / "db.sqlite3").exists()
    assert not (tmp_path / ".angee").exists()


def test_defaults_module_seeds_compose_installed_app(tmp_path: Path) -> None:
    """Defaults seed the composer app before project app roots."""

    defaults = runpy.run_module(
        "angee.compose.defaults",
        init_globals={"BASE_DIR": tmp_path, "INSTALLED_APPS": ("angee.resources",)},
        run_name="__test_defaults__",
    )

    assert defaults["INSTALLED_APPS"] == ["angee.compose", "angee.resources"]
    assert defaults["USE_TZ"] is True
    assert defaults["DEFAULT_AUTO_FIELD"] == "django.db.models.BigAutoField"
    assert defaults["ANGEE_RUNTIME_MODULE"] == "runtime"
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
        "ANGEE_RUNTIME_DIR": tmp_path / "runtime",
    }
    Composer(settings).compose_settings()

    assert settings["ALPHA_SETTING"] == "ok"
    assert settings["CHANNEL_LAYERS"]["alpha"]["BACKEND"] == "tests.Layer"
    assert settings["MIDDLEWARE"] == [
        "django.middleware.common.CommonMiddleware",
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
        autoconfig=("SETTINGS = {\n    'ANGEE_RESOURCE_SOURCE_CLASSES.url': 'alpha.sources.url_source',\n}\n"),
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    settings: dict[str, Any] = {
        "INSTALLED_APPS": ("alpha",),
        "ANGEE_RUNTIME_DIR": tmp_path / "runtime",
        "ANGEE_RESOURCE_SOURCE_CLASSES": {
            "path": "angee.resources.sources.path_source",
        },
    }
    Composer(settings).compose_settings()

    assert settings["ANGEE_RESOURCE_SOURCE_CLASSES"] == {
        "path": "angee.resources.sources.path_source",
        "url": "alpha.sources.url_source",
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
        "ANGEE_RUNTIME_DIR": tmp_path / "runtime",
    }
    Composer(settings).compose_settings()

    assert settings["MIDDLEWARE"] == [
        "django.middleware.common.CommonMiddleware",
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
        "ANGEE_RUNTIME_DIR": tmp_path / "runtime",
    }
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
        "ANGEE_RUNTIME_DIR": tmp_path / "runtime",
    }
    with pytest.raises(ImproperlyConfigured, match="depends_on"):
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
        "ANGEE_RUNTIME_DIR": tmp_path / "runtime",
    }
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
        "ANGEE_RUNTIME_DIR": tmp_path / "runtime",
    }
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
        "ANGEE_RUNTIME_DIR": tmp_path / "runtime",
    }
    with pytest.raises(ImproperlyConfigured, match="Cycle"):
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


def test_addon_contract_infers_only_bound_conventional_symbols(
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

    from angee.addons import addon_contract

    contract = addon_contract(_addon_test_config("alpha_annotations"))

    assert contract is not None
    assert contract.schemas is None
    assert contract.mcp_tools is None


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
        "ANGEE_RUNTIME_DIR": tmp_path / "runtime",
        "ALPHA_SETTING": "project",
    }
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
                'ANGEE_RUNTIME_DIR: "{BASE_DIR}/runtime"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delitem(sys.modules, "settings", raising=False)
    monkeypatch.setattr(sys, "argv", [str(manage_py)])
    monkeypatch.setenv("YAMLCONF_ALPHA_SETTING", "env")

    import angee.compose.settings as compose_settings

    compose_settings = importlib.reload(compose_settings)

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
                'ANGEE_RUNTIME_DIR: "{BASE_DIR}/runtime"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delitem(sys.modules, "settings", raising=False)
    monkeypatch.setattr(sys, "argv", [str(manage_py)])
    monkeypatch.setenv("ANGEE_ALPHA_SECRET", "env-secret")

    import angee.compose.settings as compose_settings

    compose_settings = importlib.reload(compose_settings)

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
        "ANGEE_RUNTIME_DIR": tmp_path / "runtime",
    }
    with pytest.raises(ImproperlyConfigured, match="must not define INSTALLED_APPS"):
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
        "ANGEE_RUNTIME_DIR": tmp_path / "runtime",
    }
    Composer(settings).compose_settings()

    assert settings["ANGEE_GRAPHQL_IDE"] == "custom"
