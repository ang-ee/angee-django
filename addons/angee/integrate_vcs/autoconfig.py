"""Settings contributed by the VCS capability."""

from typing import TypedDict

_Settings = TypedDict(
    "_Settings",
    {
        "ANGEE_IMPL_REGISTRIES:append": list[str],
        "ANGEE_VCS_BACKEND_CLASSES": dict[str, str],
    },
)

SETTINGS: _Settings = {
    "ANGEE_IMPL_REGISTRIES:append": ["angee.integrate_vcs.backend.VCSBackend"],
    "ANGEE_VCS_BACKEND_CLASSES": {
        "local": "angee.integrate_vcs.backend.LocalVCSBackend",
    },
}
