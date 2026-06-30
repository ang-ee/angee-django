"""The VCS/remote tier of the addon marketplace.

`platform`'s `Addon` registry reflects the *local* world — addons installed
(an entry point) or local (an `addon.toml` under a configured dir), and which
lifecycle state each is in. This addon adds the **remote** tier: addons *known
from a VCS repo* but not materialised in the project. It extends `platform.Addon`
with VCS provenance (the integrate `Source` a row was discovered from + the bearing
directory) and contributes the remote rows, discovered through the same
`VcsBridge -> Source` flow integrate already uses for templates and skills — a
`Source(kind="addon")`, marker `addon.toml`, the manifest reused as the catalog
record.

The boundary stays one-way: `platform`/`integrate` know nothing of this addon. The
sync coordinates with platform's reconcile by tier — platform owns installed/local
rows (their `source`/`state`); this addon only ever *adds* provenance to an
already-materialised row, and owns the `REMOTE` rows outright (created `DISABLED`,
marked `REMOVED` when they vanish). So pointing a local bridge at this very repo
yields provenance on the rows that are already installed, not a flood of `REMOTE`
duplicates — the coordination working.
"""
