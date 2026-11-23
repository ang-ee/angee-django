---
"@angee/ui": minor
---

`PaneRow` takes `disabled` and `disabledReason`; a reason alone makes the row
unavailable, and `null`, `false` and an empty string are no reason. An
unavailable row stays listed, muted and without a hover fill, and neither
navigates nor calls `onOpen`; it is a focusable button with `aria-disabled`,
and its reason is shown in full under the title and announced as its
description.
