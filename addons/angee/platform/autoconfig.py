"""Settings fragments required by the Angee platform console."""

from __future__ import annotations

SETTINGS = {
    # The AddonInstaller backend selection + registry (the row-less ImplClassField
    # shape). ``local`` (the dev default) edits the local settings.yaml and treats
    # rebuild as pending. The ``operator`` entry is contributed by the
    # ``platform_integrate_operator`` bridge addon (platform stays unaware of the
    # operator); a deployment flips the selection via
    # ``ANGEE_ADDON_INSTALLER_BACKEND="operator"``. See ``angee.platform.installer``.
    "ANGEE_ADDON_INSTALLER_BACKEND": "local",
    "ANGEE_ADDON_INSTALLER_BACKEND_CLASSES": {
        "local": "angee.platform.installer.LocalInstallerBackend",
    },
}
"""Django settings contributed when the platform addon is installed."""
