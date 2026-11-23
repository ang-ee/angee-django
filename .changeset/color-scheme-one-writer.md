---
"@angee/ui": patch
---

Under an `AppearanceProvider`, the user menu's light and dark toggle reads and
sets only the provider's colour scheme: it no longer mounts the browser-local
`useColorSchemePreference`, which re-applied its own scheme (`system` unless
set in that browser) to the document over the saved preference when the shell
mounted, when the system scheme changed and on a storage event. A tree without
a provider still uses the browser-local scheme.
