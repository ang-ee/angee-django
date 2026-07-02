"""Settings fragments required by Angee IAM."""

from __future__ import annotations

SETTINGS = {
    "AUTH_USER_MODEL": "iam.User",
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
        "angee.iam.auth.ModelBackend",
    ],
}
"""Django settings contributed when IAM is installed."""
