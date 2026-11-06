---
"@angee/ui": major
"@angee/metadata": major
"@angee/app": major
---

Remove exports that no framework package, addon or consumer imports; each
remains a private detail of its owner.

- `@angee/metadata`: removed `ActiveGraphQLSchemaProvider` and
  `useActiveGraphQLSchemaName`, which had no reader; `ActiveDataProviderNameProvider`
  names the active schema. Removed `refineResourcesFromSchemaMetadata`; use
  `refineResourcesFromDataResources(metadata.resources)`.
- `@angee/app`: `IDENTITY_STALE_TIME` and `useUpdatePreferences` are no longer
  exported.
- `@angee/ui`: `buildMenuTree`, `DashboardTargetSchema`, `DashboardRefreshSchema`,
  `WidgetMeasureSchema`, `WidgetSourceSchema`, `WidgetDataSpecSchema`,
  `validateDashboardLayout`, `COLOR_SCHEME_STORAGE_KEY`,
  `applyColorSchemePreference`, `resolveColorSchemePreference`,
  `systemColorScheme`, `readAppearancePreferences`, `writeAppearancePreferences`,
  `ResourceToolbarPager`, `FormViewLines`, `useRecordFieldMarksForField`,
  `relationOptionsFromRows`, `RELATION_OPTION_LIMIT` and `Many2ManyCellEdit` are
  no longer exported. The dashboard types derived from those schemas remain.
