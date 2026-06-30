"""Settings fragments required by the Angee platform console."""

from __future__ import annotations

SETTINGS = {
    # The AddonInstaller backend selection + registry (the row-less ImplClassField
    # shape). ``local`` edits the local settings.yaml and treats rebuild as pending
    # (dev default); ``operator`` is the production transport (file API + rebuild),
    # flipped on via stack settings (``ANGEE_ADDON_INSTALLER_BACKEND="operator"``).
    # See ``angee.platform.installer``.
    "ANGEE_ADDON_INSTALLER_BACKEND": "local",
    "ANGEE_ADDON_INSTALLER_BACKEND_CLASSES": {
        "local": "angee.platform.installer.LocalInstallerBackend",
        "operator": "angee.platform.installer.OperatorInstallerBackend",
    },
}
"""Django settings contributed when the platform addon is installed."""
