---
"@angee/app": minor
"@angee/ui": minor
---

A customizable theme may decline the run-time host appearance source:
`createThemeCustomizationOptions(defaults, { version, acceptsHostAppearanceSource: false })`
sets the new optional `ThemeOptionsDefinition.acceptsHostAppearanceSource`
(default true), and `defineTheme` refuses a value that is not a boolean. While the
host theme declines, `createApp` asks the `hostAppearance` source about no theme and
no build options; whenever it asks about no theme it wears none of the answer, options
or `pending`, so the build's options for its own theme apply whole. The flag
is not part of the options envelope or of the theme catalogue: no options version
changes, and saved options resolve to the same tokens.
