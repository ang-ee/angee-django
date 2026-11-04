"""URL contributions use native includes, resolution and registered checks."""

import os
import subprocess
import sys
from importlib import import_module
from types import ModuleType

import pytest
from django.apps import apps
from django.urls import resolve

from angee.graphql.apps import GraphQLConfig
from angee.messaging.apps import MessagingConfig
from angee.server.apps import ServerConfig
from angee.storage.apps import StorageConfig


def run(script):
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path)),
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_server_config_registers_native_url_check_after_setup():
    run("""
from django.conf import settings
settings.configure(INSTALLED_APPS=["angee.server"], ROOT_URLCONF="angee.server.urls", SECRET_KEY="url-check")
import django
django.setup()
from django.core.checks import Tags, run_checks
from django.core.checks.registry import registry
from django.urls import resolve
from angee.server.checks import check_url_collisions
assert check_url_collisions in registry.registered_checks
assert run_checks(tags=[Tags.urls]) == []
assert resolve("/health/ready").url_name == "angee-server-readiness"
""")


def test_enabled_framework_urls_share_the_server_urlconf(monkeypatch):
    """All four addon URL declarations resolve with the server's readiness route."""
    configs = [
        cls(name, import_module(name))
        for cls, name in (
            (ServerConfig, "angee.server"),
            (StorageConfig, "angee.storage"),
            (GraphQLConfig, "angee.graphql"),
            (MessagingConfig, "angee.messaging"),
        )
    ]
    monkeypatch.setattr(apps, "get_app_configs", lambda: configs)
    from angee.server.urls import _patterns, check_url_collisions

    root = ModuleType("composed_server_urls")
    root.urlpatterns = [pattern for _, pattern in _patterns()]
    assert check_url_collisions() == []
    for route, name in (
        ("/health/ready", "angee-server-readiness"),
        ("/storage/upload", "storage_upload"),
        ("/graphql/public/", None),
        ("/forms/example", "messaging_public_webform"),
    ):
        assert resolve(route, urlconf=root).url_name == name


def test_native_urls_and_collisions():
    script = """
import sys
from types import ModuleType
from django.conf import settings
settings.configure(INSTALLED_APPS=[], ROOT_URLCONF="angee.server.urls")
import django
django.setup()
from django.apps import AppConfig, apps
from django.urls import path, include, resolve
from django.http import HttpResponse
from django.core.checks import run_checks, Tags
view = lambda request, **kwargs: HttpResponse("ok")
leaf = ModuleType("leaf")
leaf.app_name = "leaf"
leaf.urlpatterns = [path("<int:pk>/", view, name="detail")]
sys.modules["leaf"] = leaf
outer = ModuleType("outer")
outer.urlpatterns = [path("nested/", include("leaf", namespace="inside"))]
sys.modules["outer"] = outer
class First(AppConfig):
    name = "first"
    path = "/tmp"
    compose_urls = "outer"
    compose_url_prefix = "api/"
class Second(First):
    name = "second"
class Unselected(AppConfig):
    name = "unselected"
    path = "/tmp"
    # Merely shipping urls.py never opts in.
configs = [First("first", ModuleType("first")), Second("second", ModuleType("second")),
           Unselected("unselected", ModuleType("unselected"))]
apps.get_app_configs = lambda: configs
from angee.server import checks
assert "angee.server.urls" not in sys.modules
errors = [error for error in run_checks(tags=[Tags.urls]) if error.id.endswith(".E001")]
from angee.server import urls
assert len(urls.urlpatterns) == 2
match = resolve("/api/nested/23/")
assert match.kwargs == {"pk": 23}
assert match.namespaces == ["inside"]
assert len(errors) == 1, errors
assert errors[0].id == "server.E001"
assert "first" in errors[0].msg and "second" in errors[0].msg
"""
    run(script)


SETUP = """
from types import ModuleType
from django.conf import settings
settings.configure(INSTALLED_APPS=[], ROOT_URLCONF="angee.server.urls")
import django
django.setup()
from django.apps import AppConfig, apps
from django.urls import path, re_path, include, resolve, register_converter, URLPattern, URLResolver
from django.urls.resolvers import RegexPattern, RoutePattern
def first_view(request, *args, **kwargs):
    pass
def second_view(request, *args, **kwargs):
    pass
class Contribution(AppConfig):
    path = "/tmp"
def contributions(first_patterns, second_patterns):
    configs = []
    for name, patterns in (("first", first_patterns), ("second", second_patterns)):
        module = ModuleType(name + "_urls")
        module.app_name = name
        module.urlpatterns = patterns
        config = Contribution(name, ModuleType(name))
        config.compose_urls = module
        config.compose_url_namespace = name
        config.compose_url_prefix = "api/"
        configs.append(config)
    apps.get_app_configs = lambda: configs
    from angee.server.urls import check_url_collisions
    return check_url_collisions()
"""


@pytest.mark.parametrize("nested", [False, True])
def test_disjoint_custom_converters_with_same_regex_retain_ordered_resolution(nested):
    run(
        SETUP
        + f"nested = {nested!r}\n"
        + """
calls = []
class Even:
    regex = "[0-9]+"
    def to_python(self, value):
        calls.append(("even", value))
        value = int(value)
        if value % 2:
            raise ValueError
        return value
    def to_url(self, value):
        return str(value)
class Odd(Even):
    def to_python(self, value):
        calls.append(("odd", value))
        value = int(value)
        if not value % 2:
            raise ValueError
        return value
register_converter(Even, "even")
register_converter(Odd, "odd")
if nested:
    first = path("<even:value>/", include([path("detail/", first_view)]))
    second = path("<odd:value>/", include([path("detail/", second_view)]))
    suffix = "detail/"
else:
    first = path("<even:value>/", first_view)
    second = path("<odd:value>/", second_view)
    suffix = ""
errors = contributions([first], [second])
assert not errors, errors
assert calls == [], calls
assert resolve("/api/2/" + suffix).func is first_view
assert resolve("/api/3/" + suffix).func is second_view
assert calls == [("even", "2"), ("even", "3"), ("odd", "3")], calls
"""
    )


@pytest.mark.parametrize(
    "expression",
    [
        'path("detail/", view)',
        'path("nested/", include([path("detail/", view)]))',
        'path("<int:pk>/", view)',
        're_path(r"^detail/$", view)',
        're_path(r"^(?P<part>[a-z]+)/(?P=part)/$", view)',
    ],
)
def test_identical_native_chains_collide_across_namespaces(expression):
    run(
        SETUP
        + f"first = {expression.replace('view', 'first_view')}\n"
        + f"second = {expression.replace('view', 'second_view')}\n"
        + """
errors = contributions([first], [second])
assert len(errors) == 1, errors
assert errors[0].id == "server.E001"
assert "first" in errors[0].msg and "second" in errors[0].msg
"""
    )


@pytest.mark.parametrize(
    ("first", "second", "first_url", "second_url"),
    [
        (
            're_path(r"^(?P<a>[a-z])(?P<b>[a-z])(?P=a)/$", first_view)',
            're_path(r"^(?P<b>[a-z])(?P<a>[a-z])(?P=a)/$", second_view)',
            "/api/aba/",
            "/api/abb/",
        ),
        (
            're_path(r"detail/", first_view)',
            're_path(r"^detail/", second_view)',
            "/api/prefix/detail/",
            None,
        ),
        (
            're_path(r"^detail/$", first_view)',
            're_path(r"(?i)^detail/$", second_view)',
            "/api/detail/",
            "/api/DETAIL/",
        ),
        (
            'path("nested/", include([re_path(r"^detail/$", first_view)]))',
            'path("", include([re_path(r"^nested/detail/$", second_view)]))',
            "/api/nested/detail/",
            None,
        ),
        (
            'path("detail/", first_view)',
            're_path(r"^detail/\\Z", second_view)',
            "/api/detail/",
            None,
        ),
        (
            'path("<int:pk>/", first_view)',
            'path("<int:identifier>/", second_view)',
            "/api/2/",
            None,
        ),
        (
            'URLPattern(RegexPattern(r"^detail/$", is_endpoint=True), first_view)',
            'URLPattern(RegexPattern(r"^detail/$", is_endpoint=False), second_view)',
            "/api/detail/",
            None,
        ),
    ],
)
def test_differently_expressed_patterns_retain_native_semantics(first, second, first_url, second_url):
    run(
        SETUP
        + f"errors = contributions([{first}], [{second}])\n"
        + f"assert not errors, errors\nassert resolve({first_url!r}).func is first_view\n"
        + (f"assert resolve({second_url!r}).func is second_view\n" if second_url is not None else "")
    )


def test_custom_pattern_matching_is_not_inferred_from_its_native_regex():
    run(
        SETUP
        + """
class RejectingPattern(RoutePattern):
    def match(self, value):
        raise AssertionError("checks must not call custom matching")
errors = contributions(
    [URLPattern(RejectingPattern("detail/", is_endpoint=True), first_view)],
    [path("detail/", second_view)],
)
assert not errors, errors
"""
    )


def test_identical_custom_converter_owner_is_compared_without_executing_it():
    run(
        SETUP
        + """
class Custom:
    __hash__ = None
    regex = "[0-9]+"
    def to_python(self, value):
        raise AssertionError("checks must not execute converters")
    def to_url(self, value):
        raise AssertionError("checks must not execute converters")
register_converter(Custom, "custom")
errors = contributions(
    [path("<custom:value>/", first_view)],
    [path("<custom:value>/", second_view)],
)
assert len(errors) == 1, errors
assert errors[0].id == "server.E001"
"""
    )


@pytest.mark.parametrize("resolver", [False, True])
def test_custom_resolution_is_not_inferred_from_native_pattern_metadata(resolver):
    run(
        SETUP
        + f"resolver = {resolver!r}\n"
        + """
calls = []
class Custom(URLResolver if resolver else URLPattern):
    def resolve(self, value):
        calls.append(value)
        return None
if resolver:
    first = Custom(RoutePattern(""), [path("detail/", first_view)])
    second = path("", include([path("detail/", second_view)]))
else:
    first = Custom(RoutePattern("detail/", is_endpoint=True), first_view)
    second = path("detail/", second_view)
errors = contributions([first], [second])
assert not errors, errors
assert calls == [], calls
assert resolve("/api/detail/").func is second_view
assert calls == ["detail/"], calls
"""
    )
