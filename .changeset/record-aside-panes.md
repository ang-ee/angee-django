---
"@angee/ui": major
---

The record aside is one Timeline index with drill-ins behind it and one pane
anatomy. `TimelineRail` lists the record's chronology (all or needs-me scope);
a drill-in writes `chatterTab`, `chatterTarget`, `chatterFocus` and
`chatterFrom` to the URL (`CHATTER_SEARCH_KEYS`). Pane lists compose `PaneRow`,
`PaneRows`, `PaneLabel`, `ScopeChip` and `StepList`; panes render `PaneToolbar`.
The framework owns the Links tab (`RelatedPane`, `RELATED_TAB_ID`): edge owners
declare `related#groups` or `<model>#groups` children (`RelatedGroupContent`),
and the aside badge and group labels share one count read
(`ChatterCountProbe`, `useChatterCounts`). Removed: record peeks
(`useRecordPeek`, `useRecordPeekContext`, `RecordPeekOpen`,
`RecordPeekOpenOptions`, `RecordPeekReference`) and the chatter composer slot.
