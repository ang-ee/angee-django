"""OIDC login addon: turn a verified external identity into an Angee session.

OIDC end to end, extending ``integrate``'s OAuth and composing ``iam``'s session:
it owns the ``OidcClient`` refinement of an ``OAuthClient`` (the OIDC data layer),
the OIDC protocol and ID-token verification, and the login/link flow — the bridge
that resolves a verified identity to an ``iam`` user.
"""
