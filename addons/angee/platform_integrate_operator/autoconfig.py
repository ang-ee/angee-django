"""Settings fragments required by the operator AddonInstaller backend."""

from __future__ import annotations

SETTINGS = {
    # Contribute the operator backend into platform's AddonInstaller registry.
    # Dotted key so it merges into the registry platform declares (just `local`),
    # not replaces it — a deployment then flips
    # `ANGEE_ADDON_INSTALLER_BACKEND="operator"` to use it.
    "ANGEE_ADDON_INSTALLER_BACKEND_CLASSES.operator": (
        "angee.platform_integrate_operator.installer.OperatorInstallerBackend"
    ),
}
"""Django settings contributed when the operator installer-transport addon is installed."""
