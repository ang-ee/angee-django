# @angee/app

`@angee/app` is Angee's React composition root for routes, providers, addon manifests, code generation, and shared Vite/Vitest configuration; it is the top layer and composes `@angee/refine`, `@angee/metadata`, and `@angee/ui` without pushing application concerns back down.

Install: `pnpm add @angee/app`

An addon may declare `brand: { name, mark }` once, with a registered glyph as its
mark. The host owns `home` and `confineTo`: `createApp({ ..., home: "requests.all",
confineTo: "requests" })` projects that menu root into the rail and command
palette and redirects console routes owned by other roots to home with replacement.
Unowned console routes (account, profile, preferences) remain reachable. Unknown
roots and homes outside the selected root fail composition. Public layouts remain
available; the server still owns access. The project template exposes `home`,
`confine_to` and `console_chatter` answers. Addons cannot claim the `ui` translation
namespace, which belongs to the rendered package.

Resource mutation argument names and GraphQL types are projected from generated metadata at
[`resourceMutationsForSchema`](src/resource-projection.ts) into the metadata-free
provider contract. Hosts and pages do not maintain a second capability list.

[React documentation](https://docs.angee.ai/react/) · [Package reference](https://docs.angee.ai/react/reference/app/)
