---
"@angee/refine": minor
---

A new entry point, `@angee/refine/testing/wire`, exports `graphqlWireFetch`, a
GraphQL wire fake for stories and tests: a `fetch` for the Angee GraphQL
transport that answers each root field of a request from the handler named for
it and answers the session's CSRF probe. A root field without a handler, an
unparseable document or a missing required variable fails loudly; a handler that
throws `GraphQLError` (re-exported there) answers with that error, and an
optional `onRequest` observes each parsed request. It imports no test runner,
so Storybook and Vitest can both use it; its request and handler types are
`GraphQLWireRequest` and `GraphQLWireOperation`.
