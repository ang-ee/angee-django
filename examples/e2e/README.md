# @angee-example/notes-e2e

The reference Playwright e2e suite for the notes example — the worked example a
consumer copies for their own product suite. It composes the in-repo
`@angee/e2e` harness and runs against a live, seeded Angee stack with
`example.notes` composed (the framework-dev stack's `full` addon profile).

[Checks](../../docs/checks.md) owns the stack-root commands and prerequisites.
[End-to-End Testing](../../docs/frontend/e2e.md) describes harness usage and the
shared database: a test run does not allocate or reset its own database.

The suite is run manually against a live stack; it is not part of any repo CI.
