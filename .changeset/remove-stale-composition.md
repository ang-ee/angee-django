---
"@angee/ui": major
"@angee/refine": minor
---

Remove the obsolete Storybook composition snapshot and its `@angee/ui/catalog`
exports: `CompositionReportSchema`, `CompositionReport` and
`parseCompositionReport`. The generated component catalog and the running
application's composition explanation remain available.

Appearance no longer writes or styles the deprecated `data-theme` mirror;
`data-color-scheme` owns the DOM color scheme.

Authored keyset feeds can retain typed domain metadata alongside each page,
including empty pages. Timeline uses this shared feed for its counts and kinds.
