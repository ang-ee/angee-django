---
"@angee/ui": minor
---

A relation field's `relationEdit: null`, and a related row held as its value,
are honoured by every relation control a descriptor field renders through. Four
defects stood in the way:

- The dialogs render a relation field through their own control
  (`DescriptorFieldList`), which passed the picker neither the field's edit
  opt-out nor a known option. In a dialog `relationEdit: null` still offered
  Edit, and a field seeded with the related row (`{ id, <label field> }`) read
  the record again only to show its name. It now passes both, as the record
  form's field and the descriptor control do.
- An action's relation argument could not declare the opt-out, although it
  renders through the same dialog control: `ActionRelationArg` now takes
  `relationEdit` from the descriptor's own declaration. An action's relation
  argument still holds a bare id (it is submitted as one), so its record is
  read for its label.
- A registered widget did not receive the opt-out: the field handed to it by a
  form, a dialog or a line's cell left the member out. `relationEdit` is now
  declared on `FieldPresentation`, so a widget's `field` carries it, and a
  widget that renders a relation picker passes it on as the picker's `edit`.
  The party picker and the calendar picker do; the party picker also shows the
  label of a row it is given without reading the record.
- `relationSelectedOption` made an option of any row with an id, labelled by
  the id when the row did not hold its label member, and a picker given an
  option reads nothing: a relation value such as `{ id }` showed its raw id. It
  now returns `undefined` when the row's label path reads `undefined`, so the
  picker reads the record for its label. A member held as `null` or blank still
  labels the option by its id, with no read.

What else reads through `relationSelectedOption` follows the same rule: a
multi-relation field reads the labels of such rows instead of showing their
ids, and a board lane whose rows do not hold the lane relation's label shows the
unknown-value label instead of an id.

Not changed: a form spec (a JSON Schema form) cannot declare the opt-out; a
descriptor field, an authored line field and an action's relation argument can.
