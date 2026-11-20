---
"@angee/app": minor
"@angee/ui": minor
---

An addon web package can declare a standalone page in `web/package.json`
`angee.pages`: an HTML entry, the URL paths it answers and the media type a
request asks for it with. `defineAngeeWebViteConfig` builds each declared page
as `<id>.html` beside `index.html`, serves it in development at its paths to
a `GET` or `HEAD` whose `Accept` header matches the manifest's `pageAccept`
pattern, and serves the built file the same way in `vite preview` (the build
describes its pages in `.angee/pages.json` inside the distribution).
`generateWebRuntime` returns the resolved pages and the manifest's `pageCsp`,
and fails when a declared entry is missing; the build fails when a declared
page is not emitted, or when its document references `../` or uses `srcset`.

A page's document is given the design tokens and the installed themes as
stylesheets (`@angee/ui/tokens.css`, a new export of the token sheet alone, and
`runtime/web/themes.css`), not the host's own stylesheet. It carries no content
security policy of its own: the ingress sends the manifest's `pageCsp` as a
header, preview does the same, and development writes it into the document
after Vite's own head scripts. A host needs no change.

`@angee/ui/theme-runtime` exports `resolveAppearance`, the one resolution of
what a document wears from the build's appearance payload (the host's theme,
its forced or system color scheme and its token layers, or a named installed
theme), which `bootstrapAppearance` now uses, and `readAppearancePayload` for a
page, which is given that payload as a JSON data block in place of the inline
first-paint script.
