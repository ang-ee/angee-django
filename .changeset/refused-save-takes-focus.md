---
"@angee/ui": minor
---

A refused save brings the person to its reason. A record form's banner sits at
the top of its sheet; with the sheet scrolled down to the field being edited, a
save the server refused showed nothing where the person was looking, and focus
fell to the page because Save was disabled while it ran. Now, as the form's own
required check already does:

- the answer moves focus to the first shown field it names, in the form's
  order, which also scrolls it into view;
- a refusal that names no shown field (a form-level reason, a field the form
  does not display) moves focus to the banner, which is focusable for that;
- Save stays focusable while a save runs (`aria-disabled`, with `aria-busy`),
  so the keyboard user who pressed it keeps their place.

`applyFormErrors` takes `focus: true` for the first of these, through the form
library's own `shouldFocus`; the record form's save passes it. A dialog, and an
owner that projects issues while the person edits elsewhere, do not.

Upgrade: a test that expected Save to carry `disabled` while a save is pending
finds `aria-disabled="true"`; a test that reads `document.activeElement` after
a refused save finds the refused field or the banner.
