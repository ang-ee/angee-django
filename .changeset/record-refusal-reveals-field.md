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
pane stays.

`revealField` is the only thing that selects a pane for a refused save. The
trailing lines selected their own pane after any failed save that touched them,
beside it; they no longer do. So a line refused beside a reason no field shows,
and a save refused because the lines changed elsewhere, leave the selected pane
and say it on the banner; a pane group's field refused together with a line
ends on that field, in its pane, the form's fields coming before its lines; and
a pane the person picks after a refusal stays selected: when the pane strip goes
and returns, and on a record whose caller holds its pane (a resource list's
record panel), where every pane picked after a refused line was sent back to the
lines. A form whose only pane is the lines, shown without a strip, writes no
pane for its caller on a refusal. The form's own check failing under a line
selects the lines, as before;
failing in a pane group's field and under a line at once it selects that field's
pane, while the form library's own focus still aims at the line's cell, which
that pane hides.
