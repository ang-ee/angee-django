---
"@angee/ui": patch
---

Scope action-menu return focus to the immediate dialog so nested dialogs restore
their own trigger and dialog actions render as buttons. Record menu verbs and
typed-args dialogs compose the shared action owners. ActionMenu accepts alignment
and shows loading while contributed ActionTriggers are pending. ActionTrigger
accepts native button attributes with a fixed button type. Remove the unpublished,
unused RecordActionTrigger compatibility alias.
