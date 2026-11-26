---
"@angee/ui": minor
---

A refused save that carries a reason no field shows brings the person to the
banner. A refusal may name a shown field and also carry a form-level reason;
focus went to the field, the banner at the top of the sheet stayed out of view,
and the form-level reason, the one that explains the rest, was not seen.

- With a reason no field shows (a form-level one, or one of a field the form
  does not display), the banner takes focus, and it also carries the line that
  names the refused fields ("Please fix the highlighted fields: …"), which it
  showed only when there was no form-level reason. The one focused place says
  everything; each field keeps its own reason and its mark.
- With field reasons only, the first shown field takes focus, as before.

`applyFormErrors` decides which, for a surface that passes it `focus` and a
`fieldSummary`: today the record form. A surface that passes no `focus` moves
none, and one that passes no `fieldSummary` shows no line naming the fields;
the two dialogs pass neither yet, so their list and their focus are unchanged.
The `focus` option, added in this release as `true`, is now the surface's way
of focusing its banner: a function, given by the new
`useRefusalBannerFocus(messages)`, whose `banner` props are spread on the
surface's `ErrorBanner`; `messages` is the list `formErrorsFromForm` reads.
Both are exported. The record form composes them through its save surface
(`saveBanner`).

Upgrade: replace `focus: true` with the `focusBanner` of
`useRefusalBannerFocus`, and spread its `banner` on the banner. A test of a
refusal with both a field reason and a form-level reason finds the line naming
the fields in the banner's list, and focus on the banner.
