---
"@angee/ui": major
---

The record change-history chatter tab is History. The framework placeholder is
`chatter.history` (formerly `chatter.activity`) and messaging supplies
`messaging.history` (formerly `messaging.activity`); `ChatterTabId` names
`messaging.history`, and the placeholder's messages are `chatter.tabHistory`,
`chatter.noHistory` and `chatter.historyHint`. "Activity" now names only the
kind of a record task. The former ids `activity`, `chatter.activity` and
`messaging.activity` stay as `?chatterTab=` aliases; aside `only`, `except` and
`hide` rules must name the new ids now.
