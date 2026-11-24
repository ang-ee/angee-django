---
"@angee/ui": minor
---

A field's `prefill` receives the form's current values as its second argument,
after the field's new value (this field's scope in a nested form), in record
forms and in the dialogs that render a `DescriptorFieldList` (`MutationDialog`,
`ActionFormDialog`), so a preset can derive its seeds from siblings, such as an
All day toggle turning the bounds it shows into whole days. In a record form, a
target a preset writes that the new values hide is dropped from the draft, and a
dropped field that shows again takes its loaded value back. Saving an existing
record calls `prefill` again for each visible preset field whose value changed,
and writes its seed for every target that is hidden now and was loaded with a
different value (a `prefillPreserveDirty` preset writes only the targets its
`prefillReplace` names). The record form's rail shows a field exactly when the
sheet would: visible for the current values (`showWhen`, through `resolve`) and
not `hidden`. Upgrade: make every `prefill` a pure function of its arguments.
