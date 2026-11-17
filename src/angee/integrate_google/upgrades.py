"""Data upgrades this addon's release declares; projects run them from their native migrations.

Each callable is a ``RunPython`` operation over historical models (docs/composer.md,
"Data upgrades"). A project's migration depends on the schema migration that gives
``integrate.OAuthClient`` its ``issuer`` and ``jwks_uri``.
"""

from __future__ import annotations

from typing import Any

from django.apps.registry import Apps
from django.db.backends.base.schema import BaseDatabaseSchemaEditor

from angee.integrate_google.providers import GoogleType
from angee.resources.models import move_seed_ledger

PRESET_ENDPOINT_FIELDS = (
    "discovery_url",
    "issuer",
    "jwks_uri",
    "authorize_endpoint",
    "token_endpoint",
    "revoke_endpoint",
    "userinfo_endpoint",
)
GEMINI_SLUG = "gemini"
"""Google's public Gemini client keeps its own narrow scope catalogue."""


def align_google_client(row: Any) -> list[str]:
    """Fill what the preset owns on one existing Google client; return the changed field names.

    Blank endpoints and OIDC metadata take the preset's values, the scope catalogue
    gains the preset's scopes and the authorize parameters gain the preset's keys
    (``include_granted_scopes``). Values an operator set, the client id and the
    secret are never replaced.
    """

    preset = GoogleType.effective_defaults()
    changed = [field for field in PRESET_ENDPOINT_FIELDS if not getattr(row, field)]
    for field in changed:
        setattr(row, field, preset[field])
    catalogue = list(dict.fromkeys([*(row.scopes_catalogue or []), *preset["scopes_catalogue"]]))
    if catalogue != list(row.scopes_catalogue or []):
        row.scopes_catalogue = catalogue
        changed.append("scopes_catalogue")
    params = {**preset["authorize_params"], **(row.authorize_params or {})}
    if params != (row.authorize_params or {}):
        row.authorize_params = params
        changed.append("authorize_params")
    return changed


def align_google_clients(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Align every existing ``google`` client but Gemini with the preset (``align_google_client``)."""

    clients = apps.get_model("integrate", "OAuthClient")._base_manager.using(schema_editor.connection.alias)
    for row in clients.filter(provider_type=GoogleType.key).exclude(slug=GEMINI_SLUG).order_by("pk"):
        if changed := align_google_client(row):
            row.save(update_fields=changed)


def rehome_gemini_seed(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Hand the ``oauth_gemini`` seed's ledger row from ``integrate`` to this addon, which now ships it."""

    move_seed_ledger(
        apps, schema_editor, xrefs=("oauth_gemini",), from_addon="angee.integrate", to_addon="angee.integrate_google",
    )
