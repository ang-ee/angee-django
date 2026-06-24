"""Django settings module for composed Angee hosts."""

from __future__ import annotations

import importlib
import os
import runpy
import sys
from collections.abc import Iterable
from pathlib import Path
from types import ModuleType

import django_yamlconf
import environ
from django.core.exceptions import ImproperlyConfigured

from angee.compose import autoconfig as _autoconfig
from angee.compose.composer import Composer
from angee.paths import resolve_path

project_dir_env = "ANGEE_PROJECT_DIR"
project_settings_env = "ANGEE_PROJECT_SETTINGS"
project_yaml_name = "settings"
defaults_settings_module = "angee.compose.defaults"
yamlconf_predefined_settings = {
    "CPU_COUNT",
    "OS_MACHINE",
    "OS_NODE",
    "OS_PROCESSOR",
    "OS_RELEASE",
    "OS_SYSTEM",
    "PYTHON",
    "TOP_DIR",
    "USER",
    "VIRTUAL_ENV",
}
env = environ.Env()


def prepend_import_paths(paths: Iterable[Path]) -> None:
    """Put import paths at the front of ``sys.path`` preserving order."""

    for import_path in reversed(tuple(path.resolve() for path in paths if path.exists())):
        sys_path_entry = str(import_path)
        if sys_path_entry in sys.path:
            sys.path.remove(sys_path_entry)
        sys.path.insert(0, sys_path_entry)


# Reloads reuse the module object, so remove previously composed Django settings
# before rebuilding the namespace from the current project contract.
for setting in list(globals()):
    if setting == _autoconfig.YAMLCONF_ATTRIBUTES or _autoconfig.is_setting_name(setting):
        globals().pop(setting, None)

# 1. Find the project root and project settings module.
project_settings_module = env.str(project_settings_env, default="settings")
if configured_project_dir := env.str(project_dir_env, default=None):
    project_dir = resolve_path(configured_project_dir)
else:
    argv0 = Path(sys.argv[0]).resolve()
    if argv0.name == "manage.py":
        project_dir = argv0.parent
    else:
        cwd = Path.cwd().resolve()
        if (cwd / "settings.yaml").exists() or (cwd / "settings.py").exists():
            project_dir = cwd
        else:
            raise ImproperlyConfigured(
                "angee.compose.settings needs ANGEE_PROJECT_DIR or a settings.yaml/settings.py project root"
            )

# 2. Make the project importable before loading Python settings. Addon source
# roots are configured by ANGEE_ADDON_DIRS after yamlconf/defaults load.
prepend_import_paths((project_dir,))

# 3. Load or synthesize the project settings module.
project_settings: ModuleType | None = None
if existing_project_settings := sys.modules.get(project_settings_module):
    existing_settings_file = getattr(existing_project_settings, "__file__", None)
    existing_settings_path = Path(str(existing_settings_file)).resolve() if existing_settings_file else None
    if existing_settings_path is not None and project_dir in existing_settings_path.parents:
        project_settings = existing_project_settings
    else:
        sys.modules.pop(project_settings_module, None)

project_settings_path = project_dir.joinpath(*project_settings_module.split(".")).with_suffix(".py")

if project_settings is None and project_settings_path.exists():
    if project_dir not in project_settings_path.resolve().parents:
        resolved_settings_path = project_settings_path.resolve()
        raise ImproperlyConfigured(
            f"Loaded settings module {resolved_settings_path} is outside configured project root {project_dir}"
        )
    project_settings = importlib.import_module(project_settings_module)
elif project_settings is None and (project_dir / "settings.yaml").exists():
    project_settings = ModuleType(project_settings_module)
    project_settings.__file__ = str(project_dir / f"{project_yaml_name}.py")
    sys.modules[project_settings_module] = project_settings

if project_settings is None:
    raise ImproperlyConfigured("angee.compose.settings needs settings.py or settings.yaml beside manage.py")

settings_file = getattr(project_settings, "__file__", None)
if not settings_file:
    raise ImproperlyConfigured("Loaded settings module has no __file__; cannot verify project root")
settings_path = Path(str(settings_file)).resolve()
if project_dir not in settings_path.parents:
    raise ImproperlyConfigured(
        f"Loaded settings module {settings_path} is outside configured project root {project_dir}"
    )

# 4. Let django-yamlconf apply YAML and environment settings.
with _autoconfig.fail_on_yamlconf_errors():
    django_yamlconf.load(
        settings=project_settings,
        base_dir=str(project_dir),
        project=project_yaml_name,
    )

# 5. Reject yamlconf's implicit ancestor settings.yaml cascade.
allowed_yamlconf_sources = {
    _autoconfig.YAMLCONF_INTERNAL_SOURCE,
    _autoconfig.YAMLCONF_ENVIRONMENT_SOURCE,
    project_settings_module,
}
project_yaml = (project_dir / "settings.yaml").resolve()
if project_yaml.exists():
    allowed_yamlconf_sources.add(str(project_yaml))
if final_conf := os.environ.get("YAMLCONF_CONFFILE"):
    allowed_yamlconf_sources.add(str(resolve_path(final_conf)))

for attribute in getattr(project_settings, _autoconfig.YAMLCONF_ATTRIBUTES, {}).values():
    sources = [attribute.get("source"), *(source for _value, source in attribute.get("history", ()))]
    for source in sources:
        if source in allowed_yamlconf_sources:
            continue
        try:
            source_path = str(resolve_path(str(source)))
        except ImproperlyConfigured, OSError, TypeError, ValueError:
            source_path = str(source)
        if source_path not in allowed_yamlconf_sources:
            raise ImproperlyConfigured(f"Unexpected django-yamlconf source {source!r}")

# 6. Evaluate Angee defaults with the project settings as the seed.
seed = {
    name: value
    for name, value in vars(project_settings).items()
    if _autoconfig.is_setting_name(name) and name not in yamlconf_predefined_settings
}
seed.setdefault("BASE_DIR", project_dir)

globals().update(
    {
        name: value
        for name, value in runpy.run_module(
            defaults_settings_module,
            init_globals=seed,
            run_name=f"{defaults_settings_module}.__effective__",
        ).items()
        if (name == _autoconfig.YAMLCONF_ATTRIBUTES or _autoconfig.is_setting_name(name))
        and name not in yamlconf_predefined_settings
    }
)

if hasattr(project_settings, _autoconfig.YAMLCONF_ATTRIBUTES):
    globals()[_autoconfig.YAMLCONF_ATTRIBUTES] = getattr(project_settings, _autoconfig.YAMLCONF_ATTRIBUTES)

# 7. Make configured addon dirs importable, then resolve installed apps and
# graph-derived settings.
prepend_import_paths((*globals().get("ANGEE_ADDON_DIRS", ()), project_dir))
Composer(globals()).compose_settings()
