---
"@angee/ui": major
---

Record chronology is the `timeline.rail` tab under `record#aside`. Addons
contribute kinds through `timeline#kinds`; each kind opens its owner's drill-in
or record page. Messaging contributes the `messaging.discussion` tab and
`messaging.conversation` drill-in; provenance contributes the
`provenance.history` drill-in. Aside `only`, `except` and `hide` rules use these
contribution IDs. `ChatterTabId` is an open string, and the `?chatterTab=` aliases
`chatter.activity`, `messaging.activity`, `chatter.history` and
`messaging.history` are gone.
Also breaking: `PageBody` renders a `div` by default (the console layout owns the one
`main` landmark; pass `as="main"` on a standalone page), `decisionAttentionColumn` is
`attentionColumn`, `RecordFieldMarkButton`, `recordCommentsTab`, `recordCarriesThreads`,
`ChatterTabContent.aliases` and `MessageRow.tracking` are removed, and `RevisionsTab`
leaves ahead of the 0.4.0 window it was promised.
