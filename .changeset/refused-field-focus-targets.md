---
"@angee/ui": patch
---

A refused owner cell or progress bar now takes the focus a refusal brings it,
and a refused composite field or absent value keeps it.

- The `ownerCell` and `progressBar` widgets hand the form their control
  (`controlRef`): the owner cell its select's trigger, the progress bar its
  number input. A refusal of either focused nothing, and the banner took it.
- `useRefusalFocus` counts focus anywhere inside a field shown invalid as focus
  in a refused field, not only on the element marked `aria-invalid`. An
  `object`, `list` or `rows` field refused as a whole focuses its first member,
  and an absent value its "Set value" button; neither is marked invalid itself,
  so the banner took the focus from them.

Not changed: a refused member of an `object`, `list` or `rows` field (a reason
under `lines.0.qty`) still focuses no control, and a record form's `rows` field
refused as a whole brings focus to the banner, not to its first cell.
