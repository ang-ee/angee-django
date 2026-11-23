---
"@angee/ui": minor
---

A calendar filters by source. An occurrence source may declare a `key` (letters,
digits, `_`, `-` and `.`, unique on its page) and a `label` (content or an addon
message key, which needs a `key`; the calendar throws on a broken rule when it
renders). When the calendar has more than one source, each labelled one gets a
toolbar toggle (`ResourceToolbarViewControls.sources`), and a hidden source
stops fetching and leaves the grid. The hidden keys are resource-view state,
`ResourceViewState.hiddenSources`, set through the resource view's
`setHiddenSources`, carried in the URL as `?hiddenSources=` and kept by saved
and shipped views, so links and presets reproduce the calendar. When every
source is hidden, a note says so and offers Show all.
