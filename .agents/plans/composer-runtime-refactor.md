# Composer / Runtime Refactor Todo

- [x] Make `Composer.initialize(globals())` the settings hook.
- [x] Make `Runtime.from_django().materialize_models()` the app-loading hook.
- [x] Move shared app graph behavior to `angee.apps`.
- [x] Delete obsolete top-level `angee.runtime` / `angee.settings` wiring.
- [x] Update docs, layering tests, and imports to the new owner map.
- [x] Run formatting, type checks, and focused tests.
- [x] Fix regressions from verification.
