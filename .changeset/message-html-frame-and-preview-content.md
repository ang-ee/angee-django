---
"@angee/ui": minor
---

`MessageHtmlFrame`, now exported, is the one sandbox for a message's HTML, and
`MessagePartsView` renders through it: it runs no scripts, resolves `cid:`
references to the URLs a caller gives it (`inlineUrls`), keeps remote images
blocked until the reader chooses "Load remote images" (or `remoteImages` is
set), and opens links in a new tab with no opener and no referrer. An HTML part
in `MessagePartsView` therefore no longer loads remote images by default.
`PreviewFileContent`, now exported, fetches a preview file and owns its loading
and error states for any `read`; it aborts a pending fetch when the URL changes,
and a read may register a release (`onCleanup`, on its `PreviewFileReadContext`)
for what it creates. A preview provider may declare `maxSize`, so a larger file
of known size falls through to the next matching provider
(`resolvePreviewProvider` takes the size as a third argument).
