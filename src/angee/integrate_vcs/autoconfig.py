"""Settings contributed by the VCS capability."""

from typing import TypedDict

_Settings = TypedDict(
    "_Settings",
    {
        "ANGEE_VCS_BACKEND_CLASSES": dict[str, str],
    },
)

SETTINGS: _Settings = {
    "ANGEE_VCS_BACKEND_CLASSES": {
        "local": "angee.integrate_vcs.backend.LocalVCSBackend",
    },
}
