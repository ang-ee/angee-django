---
"@angee/ui": minor
---

The shell owns every aside. The Chatter in the console's secondary pane owns its
width, border, background, scroll and padding, so a page's inspector is a Chatter
tab whose children render straight into the panel; `RailPanel` remains the
inspector card. A console page fills the contained content pane with `h-full`.
Removed: `PageAside`, `pageAsideVariants` and `PageAsideProps`; the
`.console-route-viewport` / `.console-route-canvas` classes and the
`--breadcrumbbar-current-h` property they read; the unused `--chatter-w` theme token
and its `--spacing-chatter-w` alias.
