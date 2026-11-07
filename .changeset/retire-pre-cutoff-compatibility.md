---
"@angee/app": major
"@angee/ui": major
---

Pre-cutoff compatibility is retired. `menus` is the dict only: the legacy
declaration list, `MenuDeclarationItem`, `isMenuDeclarationList` and the list
overloads of `defineAddon` and `defineBaseAddon` are removed. Theme definitions
lose `legacyIds`; the legacy color-scheme migration, `THEME_STORAGE_KEY`,
`LEGACY_THEME_STORAGE_KEY`, `migrateThemeCustomization` and
`migrateThemeCustomizationFromV1` are removed, and the appearance cache moves to
`angee:appearance:3` (schema 3). A `FormOverrideMap` entry is a
`RuntimeFormRegistration`, never a bare element. `GanttNode` is removed.
