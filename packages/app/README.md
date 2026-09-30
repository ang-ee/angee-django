# @angee/app

`@angee/app` is Angee's React composition root for routes, providers, addon manifests, code generation, and shared Vite/Vitest configuration; it is the top layer and composes `@angee/refine`, `@angee/metadata`, and `@angee/ui` without pushing application concerns back down.

Install: `pnpm add @angee/app`

The rendered host calls `bootApp({ target, loadSchemas, create })` to load
generated metadata before composition. `create` receives the loaded schemas and
returns the app to mount. See the [frontend guidelines](../../docs/frontend/guidelines.md).

[React documentation](https://docs.angee.ai/react/) · [Package reference](https://docs.angee.ai/react/reference/app/)
