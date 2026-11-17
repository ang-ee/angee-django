"""OIDC login addon: turn a verified external identity into an Angee session.

OIDC login end to end, extending ``integrate``'s OAuth and composing ``iam``'s
session: it contributes OIDC login policy fields onto ``OAuthClient``, owns the
nonce-bound OIDC login request, and provides the login/link flow that resolves an
ID token ``integrate`` verified to an ``iam`` user. Google sign-in composes the
``integrate_google`` preset.
It composes ``parties`` only to record a verified login email as best-effort
contact bookkeeping after identity resolution succeeds.
"""
