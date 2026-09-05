"""Settings fragments required by Angee IAM."""

from __future__ import annotations

SETTINGS = {
    "AUTH_USER_MODEL": "iam.User",
    # IAM owns the platform-admin role used by its schema and bootstrap command.
    "REBAC_UNIVERSAL_ADMIN_ROLE": "angee/role:admin",
    "MIDDLEWARE:append": [
        "django.contrib.sessions.middleware.SessionMiddleware",
        "django.middleware.csrf.CsrfViewMiddleware",
        "django.contrib.auth.middleware.AuthenticationMiddleware",
        "rebac.middleware.ActorMiddleware",
        "angee.iam.middleware.BearerTokenCsrfExemptMiddleware",
        "simple_history.middleware.HistoryRequestMiddleware",
        "reversion.middleware.RevisionMiddleware",
        "axes.middleware.AxesMiddleware",
    ],
    "AUTHENTICATION_BACKENDS:append": [
        "axes.backends.AxesStandaloneBackend",
        "rebac.backends.auth.RebacBackend",
        "angee.iam.auth.ModelBackend",
    ],
}
"""Django settings contributed when IAM is installed."""
