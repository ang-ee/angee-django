"""Serving ASGI composition over native Django HTTP and async contexts."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from dataclasses import dataclass, field
from typing import cast

import django
from asgiref.typing import ASGI3Application, ASGIReceiveCallable, ASGISendCallable, Scope
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.core.handlers.asgi import ASGIHandler

from angee.server.signals import connect_web_statement_timeout

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings")
if not apps.ready:
    django.setup(set_prefix=False)
connect_web_statement_timeout()


@dataclass(frozen=True)
class AsgiContribution:
    """Mount paths and scope handlers; lifespan factories return fresh async contexts.

    Mounts are absolute decoded paths without a trailing slash (except `/`).
    The mounted app receives the original path with its mount added to root_path,
    following ASGI's path/root_path convention. Query strings are unchanged.
    HTTP and lifespan are reserved scope types.
    """

    http_mounts: Mapping[str, ASGI3Application] = field(default_factory=dict)
    lifespans: tuple[Callable[[], AbstractAsyncContextManager[object]], ...] = ()
    protocols: Mapping[str, ASGI3Application] = field(default_factory=dict)


class _Application:
    def __init__(self) -> None:
        apps.check_apps_ready()
        if not apps.ready:
            raise RuntimeError("ASGI composition requires completed django.setup()")
        self.http = cast(ASGI3Application, ASGIHandler())
        self.mounts: dict[str, ASGI3Application] = {}
        self.protocols: dict[str, ASGI3Application] = {}
        self.lifespans: list[Callable[[], AbstractAsyncContextManager[object]]] = []
        mount_owners: dict[str, str] = {}
        protocol_owners = {"http": "Django HTTP fallback", "lifespan": "Angee server lifespan"}
        for config in apps.get_app_configs():
            factory = getattr(config, "compose_asgi", None)
            if factory is None:
                continue
            contribution = factory()
            if not isinstance(contribution, AsgiContribution):
                raise ImproperlyConfigured(f"{config.name}.compose_asgi() must return AsgiContribution")
            for prefix, handler in contribution.http_mounts.items():
                if (
                    not isinstance(prefix, str)
                    or not prefix.startswith("/")
                    or (prefix != "/" and prefix.endswith("/"))
                ):
                    raise ImproperlyConfigured(f"{config.name}: invalid HTTP mount {prefix!r}")
                self._claim(mount_owners, prefix, config.name)
                self.mounts[prefix] = handler
            for protocol, handler in contribution.protocols.items():
                self._claim(protocol_owners, protocol, config.name)
                self.protocols[protocol] = handler
            self.lifespans.extend(contribution.lifespans)
        self.prefixes = sorted(self.mounts, key=lambda prefix: (-len(prefix), prefix))

    @staticmethod
    def _claim(owners: dict[str, str], key: str, owner: str) -> None:
        if key in owners:
            raise ImproperlyConfigured(f"ASGI {key!r} is claimed by {owners[key]} and {owner}")
        owners[key] = owner

    async def __call__(self, scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable) -> None:
        kind = scope["type"]
        if scope["type"] == "lifespan":
            await self._lifespan(receive, send)
        elif scope["type"] == "http":
            root = scope.get("root_path", "")
            relative = scope["path"].removeprefix(root)
            for prefix in self.prefixes:
                if prefix == "/" or relative == prefix or relative.startswith(prefix + "/"):
                    mounted = scope.copy()
                    mounted["root_path"] = root + ("" if prefix == "/" else prefix)
                    await self.mounts[prefix](mounted, receive, send)
                    return
            await self.http(scope, receive, send)
        elif kind in self.protocols:
            await self.protocols[kind](scope, receive, send)
        else:
            raise ValueError(f"Unsupported ASGI scope type: {kind!r}")

    async def _lifespan(self, receive: ASGIReceiveCallable, send: ASGISendCallable) -> None:
        if (await receive())["type"] != "lifespan.startup":
            raise ValueError("Expected lifespan.startup")
        started = False
        try:
            async with AsyncExitStack() as stack:
                for factory in self.lifespans:
                    await stack.enter_async_context(factory())
                started = True
                await send({"type": "lifespan.startup.complete"})
                if (await receive())["type"] != "lifespan.shutdown":
                    raise ValueError("Expected lifespan.shutdown")
        except Exception as error:
            if started:
                await send({"type": "lifespan.shutdown.failed", "message": str(error)})
            else:
                await send({"type": "lifespan.startup.failed", "message": str(error)})
        else:
            await send({"type": "lifespan.shutdown.complete"})


application = _Application()
