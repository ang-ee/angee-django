"""Settings fragments required by Angee integration."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TypedDict

from angee.integrate.constants import ENSURE_SESSIONS_TASK, RECONCILER_INTERVAL

_Settings = TypedDict(
    "_Settings",
    {
        "CELERY_BEAT_SCHEDULE:append": dict[str, dict[str, object]],
        "ANGEE_INTEGRATE_OAUTH_CLIENTS": Sequence[Mapping[str, object]] | Mapping[str, Mapping[str, object]],
        "ANGEE_INTEGRATE_OAUTH_STATE_TTL": int,
        "ANGEE_OAUTH_PROVIDER_TYPE_CLASSES": dict[str, str],
        "ANGEE_RESOURCE_SOURCE_CLASSES.url": str,
    },
)

SETTINGS: _Settings = {
    "CELERY_BEAT_SCHEDULE:append": {
        "integrate.sync_due_bridges": {
            "task": "integrate.sync_due_bridges",
            "schedule": 60.0,
        },
        ENSURE_SESSIONS_TASK: {
            "task": ENSURE_SESSIONS_TASK,
            "schedule": RECONCILER_INTERVAL,
        },
    },
    # OAuth connection substrate. Host-provided OAuth client registrations (secrets
    # included) are declared here and synced by ``manage.py oauth_clients``; the
    # public catalogue is seeded from install-tier resources instead. The TTL bounds
    # the single-use redirect state record (shared by connect and OIDC login). OIDC
    # discovery TTL belongs to the ``iam_integrate_oidc`` addon.
    "ANGEE_INTEGRATE_OAUTH_CLIENTS": (),
    "ANGEE_INTEGRATE_OAUTH_STATE_TTL": 600,
    "ANGEE_OAUTH_PROVIDER_TYPE_CLASSES": {
        "generic_oauth2": "angee.integrate.oauth.providers.GenericOAuth2",
    },
    # Networked resource manifests belong to integrate's outbound HTTP owner; the
    # resources addon reads the settings registry lazily when entries materialize.
    "ANGEE_RESOURCE_SOURCE_CLASSES.url": "angee.integrate.resource_source.UrlSource",
}
"""Django settings contributed when the integrate addon is installed."""
