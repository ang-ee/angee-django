---
"@angee/app": minor
"@angee/ui": minor
---

An app may lock its theme: an app root's menu entry or an `ANGEE_UI.shell.apps`
entry sets `themeLocked: true` beside its `theme` (`createApp` refuses a lock
without one; `ANGEE_UI.shell` itself takes none). Within that app the locked
theme and the host's options replace the person's, while their colour scheme
still applies; their saved theme is kept for other apps, and the first-paint
cache keeps their own theme, options and scheme. `AppearanceProvider` takes a
`themeLocked` prop and `AppearanceState` exposes `themeLocked`.
`RadioGroupItem` now puts `aria-describedby` on the focusable radio instead of
its label.
