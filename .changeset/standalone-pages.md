---
"@angee/app": minor
---

An addon web package can declare a standalone page in `web/package.json`
`angee.pages`: an HTML entry, the URL paths it answers and the media type a
request asks for it with. `defineAngeeWebViteConfig` builds each declared page
as `<id>.html` beside `index.html` and serves it in development at its paths to
a `GET` or `HEAD` whose `Accept` header matches the manifest's `pageAccept`
pattern. `generateWebRuntime` returns the resolved pages and fails when a
declared entry is missing; the build fails when a declared page is not emitted.
A host needs no change.
