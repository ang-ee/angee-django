---
"@angee/ui": minor
---

A `PreviewFile` may carry `identity` (`{ id, version }`: the public file id and
its content digest). `PreviewPane` keeps the URL it loaded, and its renderer
stays mounted, while the identity stays the same, so a download URL that
rotates its credential no longer reloads the preview, and a changed file or
content loads the current URL; a file without `identity` is keyed by its URL as
before. The new `usePreviewUrl(file)` gives any other renderer, such as a
gallery image, the same rule.
