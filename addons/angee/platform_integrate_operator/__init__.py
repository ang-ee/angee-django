"""The operator transport for addon install/uninstall.

`platform`'s `AddonInstaller` edits `settings.yaml`'s `INSTALLED_APPS` over a
pure-transport backend, with a `local` dev stub that edits the file in place. This
addon contributes the **`operator`** backend: in a real deployment the operator
owns the project's files and the rebuild lifecycle, so the edit and the rebuild
both go through it — `read`/`write` over the operator file API
(`GET`/`PUT /files`), the rebuild over `POST /stack/build`.

It is a bridge: `platform` and `operator` are siblings (neither depends on the
other), and the operator backend needs the operator daemon client, so it lives
here — `depends_on = ["angee.platform", "angee.operator"]`, exactly the shape
`platform_integrate_vcs` uses to bridge platform+integrate. Its `autoconfig`
contributes the backend into platform's installer registry under the `operator`
key; a deployment flips `ANGEE_ADDON_INSTALLER_BACKEND="operator"` to use it. The
boundary stays one-way: `platform`/`operator` know nothing of this addon.
"""
