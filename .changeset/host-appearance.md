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
session frame, above every layout, and passes its `HostAppearanceRead`
(`{ options, pending }`) to the appearance provider; where those options rank is
the appearance guide's "Host and user precedence". `HostAppearanceDefaults`
gains `pending`: while it is set, `AppearanceProvider` keeps each person's first
paint, and the boot cache, where they wear the host's options (a person following
the app default, or a locked app); a person's own saved theme or customisation is
painted at once, and after the first paint every choice applies at once, a
draft's included. Identity's own hold is unchanged. Only the session frame sets
it: `CreateAppInput.appearance` is now `Omit<HostAppearanceDefaults, "pending">`.
`AppearanceState.hostOptions` keeps its identity across renders that change
nothing. A host with no source installed is unchanged.
