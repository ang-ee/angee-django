# Project frontend audit — examples/notes-angee/src/web

Scope: the exemplar project frontend (Vite + React) consumers copy. Judged
against AGENTS.md (find-the-owner, DRY), docs/guidelines.md (red flags, owning
object), docs/frontend/guidelines.md (one component tree; use shared primitives;
do not fork), docs/stack.md. Scanner candidate `web.scan.txt` was empty (0 bytes);
findings below are from a firsthand read of every file in the tree.

- id: web-001
  loc: examples/notes-angee/src/web/src/main.tsx:33
  category: find-the-owner / DRY
  severity: medium
  rule: AGENTS.md Constitution "Put behavior on the object that owns the data … prefer methods over loose helpers that decode its shape"; frontend/guidelines.md "Use shared page, view, form, table, widget, and shell primitives before adding new local state" and "One component tree. Extend or register; do not fork."
  finding: The host hand-rolls `chrome: ({ children }) => children` for the `public` shell, re-implementing the framework-owned `PassthroughChrome` (createApp.tsx:301, already the default at createApp.tsx:261); the exemplar copies framework-private behavior because `ShellConfig.chrome` is required and `PassthroughChrome` is not exported.
  fix: In @angee/base, export `PassthroughChrome` (or make `ShellConfig.chrome` optional so `createApp`'s own `?? PassthroughChrome` default applies) and have the host drop the inline arrow; the passthrough owner is the framework, not every host.
  status: open

- id: web-002
  loc: examples/notes-angee/src/web/src/main.tsx:33
  category: lifted / unearned code (redundant option)
  severity: low
  rule: docs/guidelines.md "Prefer deletion to abstraction" / red flag "the code is bigger instead of smarter"; AGENTS.md "Keep one source of truth per fact."
  finding: `requireAuth: false` on the `public` shell duplicates the framework default — `RouteScreen` already computes `requireAuth = shell?.requireAuth ?? route.shell !== "public"` (createApp.tsx:262), so a shell keyed `public` is unauthenticated by default; the explicit value restates a fact the framework already owns by shell name.
  fix: Drop `requireAuth: false`; rely on the `route.shell !== "public"` default, keeping the public-auth rule in its single owner (createApp).
  status: open
