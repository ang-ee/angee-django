---
"@angee/ui": patch
---

A refused save on a record form brings the person to its first refused field
even where the form does not show it. A field in a pane group that was not the
selected pane (another pane group, a saved-record tab, kept mounted or not, or
the trailing lines, on a saved record or a create form) had no mounted control,
or only the one of a pane opened earlier, so the refusal's focus reached
nothing and the banner took it. Now the form's surface reveals the field first,
in the same batch: `revealField` selects the pane that holds it and names it to
the sheet's sections, so a collapsed section holding it opens, and the
refusal's focus then lands on its control. A record verb whose patch the form's
own `submit` refuses (`applyPatch`, behind a `set` verb or a `run` verb's
`update`) reveals its refused field the same way. `focusField` composes the
same `revealField`, so which pane holds a field has one owner. No caller passes
anything new.

The form's own check does the same: a required field left empty in a pane that
is not selected, where Save focused nothing, selected no pane and showed
nothing, now has its pane selected and takes focus.

With a reason no field shows as well, the banner takes focus and the selected
pane stays. One exception is not new: the trailing lines select their own pane
after any failed save that touches them, so a line refused beside a reason no
field shows still moves the pane to the lines, and a pane group's field refused
together with a line ends on the lines pane with focus on the banner.
