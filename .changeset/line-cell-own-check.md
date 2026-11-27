---
"@angee/ui": minor
---

A record form's line cell shows its reasons as a record field does: the error
the form holds at the cell's path (`lines.1.label`), whether the server refused
the line there or the form's own check failed there. A cell whose widget
reports its draft invalid (a JSON cell whose text does not parse) now stops
Save at that cell: the cell is marked invalid, says the check's reason under
it, takes focus, and the lines' pane is selected; nothing is sent until the
draft parses. Before, `EditableLines` named that cell's control by its column
alone, so the check held the draft under the bare name (Save did nothing a
person could see, or marked a record field of the same name), and a line's
cells showed only the server's reasons. The check follows its line as lines are
added, duplicated, removed or reordered; a server reason does not, since the
form drops every server reason under the lines on such an operation after a
refused save, as before, while the form's own check runs again and its reasons
return at each line's new index. A cell's reasons are worded from its line's
errors by the walker a record field's go through, so a reason of the line
itself (`lines.1`) is no cell's, whatever the columns are named, and a reason
under a member of a cell's value (`lines.1.payload.inner`) keeps its path, as
an object field's member's does; a reason describes the cell's control
(`aria-describedby`); a column with a reason on any line, the server's or the
check's, cannot be hidden; a server reason shows once, under its cell, except
in a column named `ref`, where that walker never looks (a recorded defect, the
same as a record field's member of that name), and several under one cell show
as one line, as a record field's do. A `BoundDescriptorField` with a `scope` named its control by the
scope-relative name in the same way and now names it by its whole path
(`settings.payload`), where the check's reason is marked and shown.

**Upgrade:** `EditableLines` no longer takes `rowErrors`; it reads each cell's
reasons from the form it is given (`control`), so set them there at the cell's
path (`setError("lines.1.label", …)`, or `useForm`'s `errors`).
`lineRowErrorsFromDottedPaths`, `FormViewSurface.lineRowErrors` and the record
form's `serverFieldErrors` are removed: read a field's errors from the form
(`form.getFieldState(path).error`, `form.formState.errors`).

A line's own reason (under `lines.1`, no cell) and a reason of the lines as a
whole (`lines`) still show nowhere but in the banner's line naming the refused
fields.
