# Web build, codegen and package-layering pitfalls

Traps moved from the frontend guide's Pitfalls section. The layering, manifest and build contracts stay in [Package layering](../../../../docs/frontend/guidelines.md#package-layering) and its [Ownership boundaries](../../../../docs/frontend/guidelines.md#ownership-boundaries); commands stay in [Checks](../../../../docs/checks.md#frontend-packages-and-addon-fragments).

- **Generated documents are an explicit prerequisite for addon checks.** Neither
  root nor package typecheck/test scripts regenerate them automatically. After a
  schema change, refresh the host's SDL and codegen before checking consumers;
  [Composition and schema](../../../../docs/checks.md#composition-and-schema) owns the
  command order and working directories. Addon fragments resolve the composed
  stack's generated documents first. Select the controlling host's declared output rather than a stale repository-local runtime fallback.
- **Generate operator types from the daemon-owned SDL.** A hand-authored daemon
  result type drifts from the committed SDL the operator manifest feeds into the
  shared codegen pass; regenerate instead
  ([Ownership boundaries](../../../../docs/frontend/guidelines.md#ownership-boundaries)).
- **Expose every addon web package through the composed web manifest** — a
  package missing from `runtime/web/tailwind.sources.css` silently misses its
  unique arbitrary Tailwind classes, and a hand edit to that runtime CSS is
  overwritten by the next composition
  ([Ownership boundaries](../../../../docs/frontend/guidelines.md#ownership-boundaries)).
- **A new web package must enter the composed manifest and dependency graph.**
  Declare it through the addon/template owners, refresh the stack-owned install
  and generated composition, and restart the frontend through the operator (or
  run the whole-application restart after an install; see
  [Restart the running stack](../../../../docs/howto/getstarted.md#restart-the-running-stack))
  so Vite sees the new package. See [Checks](../../../../docs/checks.md) for environment setup
  and safe script invocation.
- **Install JS dependencies once at the owning stack workspace root.** Never run
  `pnpm install` inside a source slot, `packages/`, `addons/`, or `examples/`
  ([Composition and schema](../../../../docs/checks.md#composition-and-schema)): a
  nested install forks linked
  `vite`/`vitest` identities and can produce `Excessive stack depth comparing
  types 'UserConfig' and 'UserConfig'` in `vitest.shared.ts`. Fix that environment
  by removing the nested package `node_modules` and reinstalling at the root;
  do not weaken the types. Every workspace package must resolve through the one
  root virtual store.
- **Prebundled `@angee/*` source edits are cache-busted by source signature, not
  a manual wipe.** A project that consumes `@angee/*` as installed packages
  (`prebundleAngeePackages: true`) serves a workspace edit to a linked package
  stale whenever the optimizer cache outlives the edit; `defineAngeeWebViteConfig`
  re-optimizes on a changed source signature
  ([Ownership boundaries](../../../../docs/frontend/guidelines.md#ownership-boundaries)),
  so a stale page after a linked-package edit is a signature-marker defect to fix
  at that owner, never a reason to wipe the cache by hand or bump a version. The
  in-repo example excludes `@angee/*` (linked source, HMR) so this never applies
  there.
- **Start new addon web packages from `templates/addons/web`.** The Copier template
  owns the current ceremony: `defineBaseAddon`, `resourcePageRoutes`, lazy routed
  pages, `createNamespaceT`, `expectValidBaseAddon`, and package/test wiring.
  Don't copy an older addon and then manually chase drift.
- **Architecture guardrails are tests, not review folklore.** After changing a
  framework package edge, addon package dependency, or newly exported shared owner,
  update `packages/app/src/architecture-guardrails.test.ts` deliberately. It owns
  both the package tree and the explicit addon/example roots. Record the intended
  edge or public-export allowance; do not bypass it with an undeclared import or
  a second local implementation.
