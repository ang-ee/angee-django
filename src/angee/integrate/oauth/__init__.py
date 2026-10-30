"""OAuth2 connection protocol — the base every external account-connect builds on.

The base layer of the connection substrate: stateless OAuth2 authorization-code
and refresh helpers (:mod:`client`), single-use redirect state
(:mod:`state`), and the shared flow-error vocabulary (:mod:`errors`). OIDC login
extends this base in the ``iam_integrate_oidc`` addon; pure account-connect (Gemini,
Grok, Anthropic) uses only this layer and carries no login logic.
"""
