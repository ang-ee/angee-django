---
"@angee/ui": minor
---

A relation field can decline inline edit, and the quiet-call options of a
surface that renders its own failures are public.

- `relationEdit: null` on a field (a form's `Field`, a dialog's field
  descriptor, an authored line field) declines the relation picker's inline edit
  of the selected record, which is otherwise offered whenever the related
  resource has editable fields. `RelationFieldWidget` takes the same opt-out as
  `edit={null}`, beside `create={null}`. It is for a field where editing the
  related record is out of place, such as a channel chosen in a send dialog.
- `SURFACE_RENDERS_FAILURE` is exported from `@angee/ui`: the call options
  (`{ errorNotification: false }`) a surface passes on when it shows a
  submission's failure itself, as the dialogs and the action form already do,
  so the failure is told once.
