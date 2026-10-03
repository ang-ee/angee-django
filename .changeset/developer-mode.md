---
"@angee/app": minor
"@angee/ui": minor
---

Developer mode: any signed-in user turns it on from the user menu (stored in
their `developerMode` preference) or for the browser session with `?debug=1`
(`?debug=0` turns it off); the session choice wins over the preference. A debug
button beside the avatar shows the page's route, app, perspective and home on
hover and opens the composition on click; the expanded rail shows hidden and
removed menu items with each item's id and provenance; form labels and list
headers show technical field names on hover.

- `@angee/ui`: `DeveloperMenu`, `DeveloperModeMenuItem` and
  `useDeveloperFieldTitle` (chrome); `useDeveloperMode`,
  `useDeveloperModeSwitch`, `useRuntimeComposition` and
  `applyDeveloperModeSearch` (runtime); the `RuntimeComposition`,
  `RemovedMenuItem` and `HiddenMenuItem` contracts. `AppRuntime` gains
  `composition`, `activeRouteName` and `activeApp`. `ChromeMenuNode` gains
  `railChildren(includeHidden)`, and `activeTargetedChild`, `railMenuItems`,
  `settingsMenuItems` and `railPlace` take an `includeHidden` flag.
- `@angee/app`: the root route applies `?debug`; `CompiledMenus.removed`
  entries carry the `parent` they showed under and their `label`;
  `CompositionExplanation` extends `RuntimeComposition` with the typed
  `ResolvedShell`.
