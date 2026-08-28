# @angee-example/notes-e2e

The reference Playwright e2e suite for the notes example — the worked example a
consumer copies for their own product suite. It composes the `@angee/e2e`
harness (the angee-react repo) and runs against a live, seeded Angee stack with
`example.notes` composed (the framework-dev stack's `full` addon profile).

```sh
# From the stack root, with the stack up and demo data seeded:
pnpm --filter @angee-example/notes-e2e exec playwright install chromium
ANGEE_UI_PORT=<ui-port> pnpm --filter @angee-example/notes-e2e test:e2e
```

The suite is run manually against a live stack; it is not part of any repo CI.
