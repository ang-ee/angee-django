---
"@angee/ui": minor
---

`AppearanceState` has `drafting`, whether an appearance draft is open, so a
settings page can keep its draft open by it instead of by the identity of
`openDraft`. `AppearanceProvider` resolves the appearance once per input (the
saved preferences, the theme catalogue, the host defaults, the system scheme,
the theme lock and the draft), so a render that changes none of them no longer
re-applies the token layers to the document or re-reads and re-encodes the
first-paint cache.
