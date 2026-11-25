---
"@angee/app": minor
"@angee/ui": minor
---

An addon may supply the deployment's appearance at run time through the new
`AddonManifest.hostAppearance` key, a `HostAppearanceSource`. Like
`dashboardStore` it is install-one: two installed providers fail composition
with `Addons "a", "b" each provide the host appearance; install one.`, and
`ComposedAddons.hostAppearance` holds the one source or `null`. `createApp`
calls the source's `useOptions(theme, build)` hook once per render of its
session frame, above every layout, for the theme a person who chose none wears
(the selected app's, else the build's, locked apps included) and the build's
options for that theme; the `HostAppearanceRead` it returns, `{ options, pending }`,
replaces the build's options for that theme while `options` is set. A person's
own theme and options still win. `HostAppearanceDefaults` gains `pending`: while
it is set, `AppearanceProvider` keeps the first paint and the boot cache, as it
does while identity resolves; a source clears it when its read settles, fails
or pauses. A host with no source installed is unchanged.
