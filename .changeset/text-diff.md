---
"@angee/ui": minor
---

`TextDiff` shows how one text became another, word by word: removals struck
through and additions underlined, each with a visually hidden "Removed:" or
"Added:" cue. It takes a `comparison` made once by `compareText(before, after)`
(a `TextComparison` of `TextChange` runs), which two `TextDiff`s of a
comparison pair can share, one per `side` (`"before"`, `"after"`); without
`side` it shows both inline. A comparison that runs past its time budget is
shown as a whole replacement with a note. `textDiffVariants` is its style
recipe. `@angee/ui` now depends on `diff`.
