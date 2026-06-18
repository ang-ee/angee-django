"""OIDC login addon: turn a verified external identity into an Angee session.

OIDC end to end, extending ``integrate``'s OAuth and composing ``iam``'s
session: it contributes OIDC login fields onto ``OAuthClient``, owns the OIDC
protocol and ID-token verification, and provides the login/link flow that
resolves a verified identity to an ``iam`` user.
"""
