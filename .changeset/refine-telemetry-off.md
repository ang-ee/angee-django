---
"@angee/app": patch
---

The app `createApp` builds turns off Refine's telemetry (`disableTelemetry`), so
a page load no longer sends a request to `telemetry.refine.dev`. A host needs no
change.
