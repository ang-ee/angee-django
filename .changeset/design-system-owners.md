---
"@angee/ui": major
---

One owner per design-system element. `Chip` is the one pill (tone, variant,
size, shape, mark, pressed) and `CountBadge` the one count pill; `Badge` and
`Tag` are removed. `StatusBadge` is exported and `ListStatusMessage` replaces
the list status rows. Bars compose the Base UI `Toolbar`, `Button`'s icon sizes
are the only icon button and verb menus compose `ActionMenu`. `Pager` has a
record mode and `ResourcePaneFooter` owns the pane footer. The `Combobox`
primitives sit under the combobox, many2many and search box; `FieldRoot` and
`FieldLabel` own field labels; `formatTime`, `formatDayLabel`, `formatMonth` and
`formatJson` format values. Removed with their callers migrated: `Divider`,
`TextInput`, `DebouncedSearchInput`, `FieldRow`, `PopoverInput`, `ToolbarInput`,
`ChatHeaderAction`, `TimelineView`, `TimelineEntry`, `formatDuration` and the
label recipe (`labelVariants` and its variant types).
