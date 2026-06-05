"""Settings fragments required by Angee IAM."""

from __future__ import annotations

SETTINGS = {
    "AUTH_USER_MODEL": "iam.User",
    "ANGEE_IAM_OAUTH_CLIENTS": (),
    "ANGEE_IAM_OIDC_DISCOVERY_TTL": 3600,
    "ANGEE_IAM_OIDC_STATE_TTL": 600,
    "MIDDLEWARE:append": [
        "django.contrib.sessions.middleware.SessionMiddleware",
        "django.middleware.csrf.CsrfViewMiddleware",
        "django.contrib.auth.middleware.AuthenticationMiddleware",
        "rebac.middleware.ActorMiddleware",
        "angee.iam.middleware.BearerTokenCsrfExemptMiddleware",
        "simple_history.middleware.HistoryRequestMiddleware",
        "reversion.middleware.RevisionMiddleware",
    ],
    "AUTHENTICATION_BACKENDS:append": [
        "rebac.backends.auth.RebacBackend",
        "django.contrib.auth.backends.ModelBackend",
    ],
}
"""Django settings contributed when IAM is installed."""
