---
"@angee/ui": patch
---

A form dialog opens with focus on its first field. Before, every dialog built on
`DialogForm`, every `MutationDialog` and `ActionFormDialog` among them, opened
with focus on its Close button, so a person who opened one and typed at once
wrote nothing until they moved focus into a field. A field is a control that
holds a value, such as a text input, a select, a switch or a record picker; a
read-only, disabled or hidden one is passed over, and a control that only
performs an action is never one. With no field, focus stays on Close. Opened by
touch through its trigger, the dialog focuses itself, so no on-screen keyboard
rises over it. A dialog opened through its `open` prop cannot tell a touch, so
where the primary pointer is coarse every opening focuses the dialog itself, one
from the keyboard through its trigger included: a tablet with a hardware
keyboard never gets the field at the opening, while on a touch laptop, whose
primary pointer is fine, a dialog gets its field unless a touch on its trigger
opened it. A field whose widget's code is still loading when the dialog opens is
passed over, and so is a date or date-and-time field, a status bar and an empty
list, whose controls are plain buttons. With focus in a text field, Enter
submits the form when its submit is enabled, as it always did once a person had
clicked into the field; before, Enter at the opening pressed Close. This shows
in a dialog whose submit is enabled as it opens, such as one that asks only for
an optional reference before marking something done: a fresh Enter there now
confirms it. A key that is already down when the dialog opens does nothing in
its form, so the press that opened the dialog, held or released, neither submits
it nor changes its first field; a fresh press acts. Where focus is on the dialog
itself, keys do not pass through its form: a held Enter or Space does nothing
there, but a held Escape closes the dialog. Where focus goes when the dialog
closes is unchanged, and no caller passes anything new.
