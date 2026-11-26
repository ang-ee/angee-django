---
"@angee/ui": patch
---

The keyboard focus ring is drawn. `focus-ring` and `focus-ring-danger` were
plain classes inside `@layer utilities`, and Tailwind gives a variant to a
registered utility only, so every `focus-visible:focus-ring`,
`focus:focus-ring`, `focus-within:focus-ring` and
`data-[popup-open]:focus-ring` compiled to nothing: links, buttons, inputs and
the other controls that ask for a ring showed no focus indicator at all. Both
are now declared with `@utility`, as `selected` is, and a test compiles the
stylesheet and holds a rule for each variant the packages spell.

This is a visible change wherever the stylesheet is used: a 3px ring
(`--ring`, or `--ring-danger` on an invalid control) now appears on every
control that asked for one, on keyboard focus, and where a control spells it
so, on any focus, around a control whose inner field has focus, or while its
popup is open.

Upgrade: look at your own screens with the keyboard. A container of yours
that clips (`overflow-hidden` or `overflow-auto`) cuts a ring on the sides
where the control touches its edge, and a theme that sets `--ring` is seen for
the first time. A class of your own that you compose through a variant needs
`@utility` too.
