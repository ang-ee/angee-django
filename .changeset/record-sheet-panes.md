---
"@angee/ui": minor
"@angee/metadata": minor
---

A record form's own fields are always its sheet: there is no Overview tab, and labelled
groups are titled sections of the sheet. Tabs exist only beneath the sheet, for panes:
the trailing editable lines, then the saved-record tabs. One pane renders without a strip,
under its own heading. `defaultRecordTab`, `recordTab` and `?recordTab=` choose among the
panes, and an id that names no visible pane falls back to the first one. Removed:
`layout="tabs"`, `bodyTabs` (a body tab's content becomes a `<Group content>`),
`overviewTab`, `FORM_VIEW_OVERVIEW_TAB_ID`, the `form.tabOverview` message, the
vocabulary's `overview` key and `ModelMetadata.overviewLabel`.
