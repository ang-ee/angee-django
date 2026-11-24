---
"@angee/refine": minor
"@angee/app": minor
"@angee/ui": minor
---

An app built with `createApp` now recovers from a server outage on its own. A
network failure or a 502, 503 or 504 answer keeps that request's ordinary error
and starts one recovery check; only a failed check opens a connection dialog and
stops further requests, and recovery refreshes the current route and the reads
the outage refused. Failed mutations are never replayed: a write refused during
the outage is reported once the server is back.

`@angee/refine` exports `ServerConnection`, `ServerUnavailableError` and
`ServerConnectionStatus`, and `createAngeeGraphQLClient` takes a `connection`
option, the gate one app's schema clients share; `retryableQueryError` no longer
retries a `ServerUnavailableError`. An addon replaces the dialog's artwork or adds
guidance through the new `connection.lost#artwork` and `connection.lost#help`
containers. `@angee/ui` exports `AnimatedThemeLogo`, which the dialog and the
sign-in page use, and carries the dialog's `connection.*` copy. A host needs no
change.
