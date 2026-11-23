---
"@angee/ui": minor
---

`CommandBand` takes no row while none of its items renders (every group holds
only hidden or empty nodes), so a record whose contributed verbs offer nothing
shows no empty strip above its form. The new `editable` prop keeps the row of an
editable record's band in the layout, blank and hidden from assistive
technology, so the Save and Discard its first edit brings shift nothing;
`FormView` sets it from load on. A band placed inline, in a card or a dialog,
collapses either way.
