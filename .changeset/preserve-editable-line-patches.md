---
"@angee/ui": patch
---

Preserve focused inputs and concurrent cell edits when asynchronous editable-line widgets patch their row.
Standalone `EditableLines` callers now pass `form.setValue` alongside `form.control`.
