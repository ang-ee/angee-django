---
"@angee/app": minor
"@angee/ui": minor
---

Developer mode: any signed-in user can turn it on from the user menu or with
`?debug=1`. The rail then shows hidden and removed menu items with each item's
id and provenance, and a console panel shows the active route, app, perspective,
home and the composition's findings. `AppRuntime` gains `composition`,
`activeRoute` and `activeApp`; `useDeveloperMode` and `useRuntimeComposition`
read them, and `CompositionExplanation` is the runtime's `RuntimeComposition`.
