---
"@angee/app": minor
---

Optional Sentry error reporting for the SPA: `bootApp` takes
`errorReporting: { dsn, environment }`. With a DSN it imports `@sentry/react`,
starts it alongside the metadata load, and gives the app root React's
uncaught, caught and recoverable error handlers. Without one the SDK is never
loaded and React keeps its defaults. `createApp().mount` accepts React
`RootOptions`, and the `ErrorReportingInput` type is exported. Host projects pass
`import.meta.env.VITE_SENTRY_DSN` / `VITE_SENTRY_ENVIRONMENT`, which the stack
templates set from their `sentry_web_dsn` input.
