"""Bring Google OAuth clients created before ``integrate_google`` in line with its preset.

A project runs ``align_google_clients`` from a native data migration that depends on
the schema migration giving ``integrate.OAuthClient`` its ``issuer`` and ``jwks_uri``.
"""

from django.db import migrations

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
"""Google's public Gemini client keeps its own narrow scope catalogue."""


def applies(state):
    client = state.models.get(("integrate", "oauthclient"))
    return client is not None and {"issuer", "jwks_uri"} <= set(client.fields)


def align_google_client(row):
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


def align_google_clients(apps, schema_editor):
    clients = apps.get_model("integrate", "OAuthClient")._base_manager.using(schema_editor.connection.alias)
    for row in clients.filter(provider_type=GoogleType.key).exclude(slug=GEMINI_SLUG).order_by("pk"):
        if changed := align_google_client(row):
            row.save(update_fields=changed)


class Migration(migrations.Migration):
    dependencies = []
    operations = [migrations.RunPython(align_google_clients, migrations.RunPython.noop)]
