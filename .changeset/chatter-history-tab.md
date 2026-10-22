---
"@angee/ui": major
---

Record chatter has one Activity stream. The framework's `record#activity` container
merges comments, conversations, history changes with their statement sources, workflow
runs, notes, decisions and source events into the `activity` aside tab and retires the
Comments, Runs, Conversations and History tabs; `ChatterTabId` is an open string, the
`?chatterTab=` aliases `chatter.activity`, `messaging.activity`, `chatter.history` and
`messaging.history` are gone, and aside `only`, `except` and `hide` rules name `activity`.
Also breaking: `PageBody` renders a `div` by default (the console layout owns the one
`main` landmark; pass `as="main"` on a standalone page), `decisionAttentionColumn` is
`attentionColumn`, `RecordFieldMarkButton`, `recordCommentsTab`, `recordCarriesThreads`,
`ChatterTabContent.aliases` and `MessageRow.tracking` are removed, and `RevisionsTab`
leaves ahead of the 0.4.0 window it was promised.
