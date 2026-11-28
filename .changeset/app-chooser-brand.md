---
"@angee/ui": minor
"@angee/app": minor
---

Over several apps, the app chooser and the expanded rail's caption show a
declared brand's mark and name (the deployment's, the selected app's or its
menu root's), and keep the framework mark and "Apps" otherwise; the first rail
root's label and icon, which stand in for a missing brand elsewhere, are not
shown there. An unregistered mark falls back to the framework glyph.
`useDeclaredBrand()` returns the declared brand, or null, and
`AppRuntime.brandFallback` (set by `createApp`, and on `appSelection`'s result)
is `true` when the runtime brand is such a stand-in.
