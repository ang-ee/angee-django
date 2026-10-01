"""Settings contributed by the VCS capability."""

SETTINGS = {
    "ANGEE_IMPL_REGISTRIES:append": ["angee.integrate_vcs.backend.VCSBackend"],
    "ANGEE_VCS_BACKEND_CLASSES": {
        "local": "angee.integrate_vcs.backend.LocalVCSBackend",
    },
}
