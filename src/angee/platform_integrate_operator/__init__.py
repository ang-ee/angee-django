"""The operator transport for enabling addons and changing packages.

`platform`'s `AddonInstaller` edits `settings.yaml`'s `INSTALLED_APPS` over a
pure-transport backend, with a `local` backend that edits the file atomically. This
addon contributes the **`operator`** backend: in a real deployment the operator
owns the project's files, so reads and writes use its file API
(`GET`/`PUT /files`). Dependency changes queue the operator’s held ``py-sync`` job; its durable
operation record owns progress and recovery.

It is a bridge: `platform` and `operator` are siblings (neither depends on the
other), and the operator backend needs the operator daemon client, so it lives
here — native requirements name ``angee-platform`` and ``angee-operator``, the shape
`platform_integrate_vcs` uses to bridge platform+integrate. Its `autoconfig`
contributes the backend into platform's installer registry under the `operator`
key; a deployment flips `ANGEE_ADDON_INSTALLER_BACKEND="operator"` to use it. The
boundary stays one-way: `platform`/`operator` know nothing of this addon.
"""
