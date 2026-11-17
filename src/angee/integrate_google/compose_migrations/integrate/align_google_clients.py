"""Migration template: align existing Google OAuth clients with the ``google`` preset.

Copy this module into the host's ``runtime/integrate/migrations/`` under the host's
next migration name, then replace the placeholder dependencies below with the host's
own explicit migrations (never ``__latest__``). Run it after any migration that gives
a client the ``google`` provider type, so that client is aligned too.

Blank endpoints and OIDC metadata take the preset's values, the scope catalogue gains
the preset's scopes, and the authorize parameters gain the preset's keys
(``include_granted_scopes``). Values an operator set, client ids and secrets are never
replaced, and Google's public Gemini client keeps its own narrow catalogue. The reverse
is a no-op: the filled values are as valid before this release as after it.
"""

from __future__ import annotations

from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor

from angee.integrate_google.providers import GoogleType

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


def align_google_clients(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Fill what the ``google`` preset owns on every existing Google client but Gemini."""

    preset = GoogleType.effective_defaults()
    clients = apps.get_model("integrate", "OAuthClient")._base_manager.using(schema_editor.connection.alias)
    for row in clients.filter(provider_type=GoogleType.key).exclude(slug=GEMINI_SLUG).order_by("pk"):
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
        if changed:
            row.save(update_fields=changed)


class Migration(migrations.Migration):
    # Placeholder: the host's migration that adds ``OAuthClient.issuer`` and ``jwks_uri``.
    dependencies = [("integrate", "HOST_MIGRATION_ADDING_OAUTHCLIENT_ISSUER_AND_JWKS_URI")]
    operations = [migrations.RunPython(align_google_clients, migrations.RunPython.noop)]
