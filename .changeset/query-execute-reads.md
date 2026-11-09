---
"@angee/refine": minor
"@angee/ui": minor
"@angee/app": minor
---

`@tanstack/react-query` must be at least 5.102.0, the release that adds
`queryClient.query` and `infiniteQuery`. The app's route identity gate, auth
provider and view-as identity read use them in place of the deprecated
`ensureQueryData` and `fetchQuery`, so a host on an earlier 5.x release must
upgrade it.
