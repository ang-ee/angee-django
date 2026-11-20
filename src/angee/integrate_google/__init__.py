"""Google OAuth addon for Angee's integration layer.

Owns everything Google-specific about authenticating to Google: the ``google``
:class:`~angee.integrate_google.providers.GoogleType` preset (endpoints, OIDC
metadata, claims, incremental authorization and the scope catalogue every Google
consumer chooses from) and the install seeds for the deployment's ``google``
client and Google's public Gemini client. One ``google`` client serves login,
mail, calendar, drive and video consumers; each declares its own scopes
(``IntegrationImpl.oauth_scopes``). The addon carries no login policy: the OIDC
login addon composes this preset and sets that policy on the rows it signs users
in with. Registering the client: ``docs/howto/google-oauth-client.md``.
"""
