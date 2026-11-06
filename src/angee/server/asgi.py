"""Bootstrap native Django, then serve it with enabled addons' ASGI contributions.

An enabled addon's ``asgi`` module may define any of three contributions, read
once after Django setup in installed app order. ``http_mounts`` and ``lifespans``
are ordered contributions, each a value or a callable returning it
(:func:`angee.base.addons.addon_contribution`):

- ``http_mounts`` holds ``(prefix, application)`` pairs. A prefix is an
  absolute decoded path without a trailing slash (except ``/``); the mounted
  application receives the original path with its prefix added to ``root_path``.
- ``protocols()`` maps ASGI scope types to applications. ``http`` and
  ``lifespan`` are reserved for Django and the server.
- ``lifespans`` holds factories of fresh async context managers, entered in
  order at lifespan startup and exited in reverse at shutdown.

Mount and protocol collisions fail fast. Unmounted HTTP goes to Django's handler.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from typing import cast

import django
from asgiref.typing import ASGI3Application, ASGIReceiveCallable, ASGISendCallable, Scope
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.core.handlers.asgi import ASGIHandler

from angee.base.addons import addon_contribution, addon_modules
from angee.server.signals import connect_web_statement_timeout

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings")
if not apps.ready:
    django.setup(set_prefix=False)
connect_web_statement_timeout()

type _Lifespan = Callable[[], AbstractAsyncContextManager[object]]


class _Application:
    """Dispatch HTTP to addon mounts or Django, other scopes to contributed protocols."""

    def __init__(self) -> None:
        self.http = cast(ASGI3Application, ASGIHandler())
        self.mounts: dict[str, ASGI3Application] = {}
        self.protocols: dict[str, ASGI3Application] = {}
        self.lifespans: list[_Lifespan] = []
        mount_owners: dict[str, str] = {}
        protocol_owners = {"http": "Django HTTP fallback", "lifespan": "Angee server lifespan"}
        for config, module in addon_modules("asgi"):
            for prefix, handler in addon_contribution(module, "http_mounts"):
                if (
                    not isinstance(prefix, str)
                    or not prefix.startswith("/")
                    or (prefix != "/" and prefix.endswith("/"))
                ):
                    raise ImproperlyConfigured(f"{config.name}: invalid HTTP mount {prefix!r}")
                self._claim(mount_owners, prefix, config.name)
                self.mounts[prefix] = handler
            protocols: Mapping[str, ASGI3Application] = getattr(module, "protocols", dict)()
            for protocol, handler in protocols.items():
                self._claim(protocol_owners, protocol, config.name)
                self.protocols[protocol] = handler
            self.lifespans.extend(addon_contribution(module, "lifespans"))
        self.prefixes = sorted(self.mounts, key=lambda prefix: (-len(prefix), prefix))

    @staticmethod
    def _claim(owners: dict[str, str], key: str, owner: str) -> None:
        if key in owners:
            raise ImproperlyConfigured(f"ASGI {key!r} is claimed by {owners[key]} and {owner}")
        owners[key] = owner

    async def __call__(self, scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable) -> None:
        kind = scope["type"]
        if kind == "lifespan":
            await self._lifespan(receive, send)
        elif kind == "http":
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
