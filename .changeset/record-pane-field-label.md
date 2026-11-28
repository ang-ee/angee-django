---
"@angee/ui": minor
---

A record pane can name a field as the form does, and report its issues without
interrupting. A pane that reports on a field (a refusal it shows outside the
form) got the field's name and could focus it, but nothing offered the field's
label, so two reports with the same words under two fields read as two
identical lines. The form already owns the fact: the label its save banner
names a refused field by.

- `RecordPanelContext` gains `fieldLabel(name)` beside `focusField`: the text
  of the label the form shows for its field, or `undefined` for a field the
  form does not hold, shows without a label, or labels with something other
  than text.
- `RecordIssues` gains an optional `fieldLabel` prop beside `onFocusField`: an
  issue that names a field is titled by that label, and the control that
  focuses the field is described by it (`aria-describedby`), so two controls
  with the same words tell a screen reader which field each goes to. Without
  the prop, or without a label, the issue shows its message alone, as before.
- `RecordIssues` gains an optional `polite` prop, for issues about edits not
  yet submitted, which change while the person types elsewhere: the issues
  are then announced politely, a danger one included (otherwise an alert,
  which interrupts). The fragment renders one polite live region that stays
  in the page with no issues too, as an empty block, and the issues inside it
  carry no role of their own: a screen reader announces what arrives in a
  region it already knows. The tone and its colour do not change. No screen
  reader was run; the shape follows the ARIA practice for status messages.

The save banner's line that names the refused fields reads the same lookup. A
field whose label is not text is now named by its name there, where the line
printed `[object Object]`; a label of blanks likewise; a text label is
trimmed.

**Upgrade:** code that builds a `RecordPanelContext` by hand (a test of a record
tab's `render`) adds `fieldLabel`. A pane passes `context.fieldLabel` to
`RecordIssues` as it passes `context.focusField`.
