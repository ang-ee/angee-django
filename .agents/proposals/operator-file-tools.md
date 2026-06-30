# Proposal: operator workspace file tools (read/edit)

**Status:** proposal — for the angee-operator team to implement. Not built here.
**Asked by:** the Angee marketplace (install/uninstall addons). **Consumer:** the
platform console board (TS) + `platform_integrate_vcs`.

## Why

The addon marketplace lets an operator-user install/uninstall addons from a board.
"Install an addon" = add its root to the deployment's `settings.yaml`
`INSTALLED_APPS`, then rebuild + restart. The deployment **is a workspace/source**
the operator already owns (the `app` source), and the operator already owns the
**rebuild lifecycle** (`/stack/build`, `/stack/up`, `/stack/dev`). The one missing
capability is **reading and editing files inside a workspace's source over the
API** — so the console edits `settings.yaml` *through the operator* rather than the
Django app touching its own config. Keep it **generic** (read/edit any file in a
source); `settings.yaml` is just the first consumer.

This keeps the Django app a normal Django app (reads `settings.yaml` at boot, no
DB-driven settings-load), and puts config ownership where it belongs — with the
operator that already owns the stack files and the lifecycle.

## Scope

In scope: a generic, scoped **file read / write** API on the operator daemon,
modeled 1:1 on the existing `secrets` API.

Out of scope (the client/other systems own these):
- **No YAML logic.** The operator reads/writes raw bytes. The `INSTALLED_APPS`
  edit (comment-preserving) is done by the **board** (the `yaml` npm package) on
  the content it read back. Don't put settings.yaml/INSTALLED_APPS knowledge in
  the operator.
- **No rebuild here.** The board calls the existing `/stack/build` (+ restart)
  after writing. The file API does not trigger builds.

## Proposed API (mirror `secrets`)

Model the shapes, routes, client, and GraphQL on the existing secrets path
(`api/types.go` `SecretSetRequest`/`SecretRef`; `internal/operator/operator.go`
route registration with `s.auth(...)`; `internal/platformclient/client.go`
`SecretGet`/`SecretSet`; `internal/operator/schema.graphql` + resolvers + the gql
codegen).

### REST (auth-gated like `/secrets/*`)
- `GET  /files?source=app&path=<relpath>` → `{ path, source, content, etag }`
  (etag = content hash, e.g. sha256, for optimistic concurrency).
- `PUT  /files?source=app&path=<relpath>` body `{ content, etag? }` →
  `{ path, source, etag }`. If `etag` is supplied and the on-disk file has
  changed, return `409 Conflict` (don't clobber concurrent edits).
- (optional, later) `GET /files/list?source=app&path=<dir>` → directory entries.

Use query params (not a path segment) since `path` contains slashes —
`/secrets/{name}` uses a single segment, files don't.

### GraphQL (mirror `secretSet`)
- query `file(source: String!, path: String!): FileContent!`
  (`{ path, source, content, etag }`).
- mutation `fileWrite(source: String!, path: String!, content: String!, etag: String): FileRef!`.

### Typed client (mirror `SecretGet`/`SecretSet` in `platformclient/client.go`)
- `FileRead(ctx, source, path) (api.FileContent, error)`
- `FileWrite(ctx, source, path, content, etag string) (api.FileRef, error)`

## Scoping & security (the important part)

- **Resolve the source root** via the existing source-path resolution
  (`workspaceSourcePath(...)` / `source.Path` in `internal/service`). `source` is
  one of the stack's declared sources (`app`, `framework`, …) — reject unknown
  source keys.
- **Confine to the source root.** Clean the requested `path` and verify the
  resolved absolute path is **inside** the source root (reject `..` traversal,
  absolute paths, and symlink escapes — resolve symlinks then re-check the prefix).
- **Auth:** the same bearer gate as `/secrets/*` and `/stack/*` (`s.auth`).
- **Optional allowlist:** if you want to be conservative, gate writes to a
  configured set of editable paths (e.g. `settings.yaml`) declared in the stack
  manifest — see Open Questions. Reads can stay broad (within the source).

## Workspace targeting

The daemon runs for one stack (`--root`), so `source` selects among *that stack's*
sources and the marketplace only needs `source=app`. If/when one daemon serves
multiple workspaces, add an optional `workspace` param resolved via
`workspaceSourcePath(workspaceName, slot, source)` — the resolution helper already
exists; the API param is the only addition.

## How the console uses it (for context, not to implement)

1. Board reads `app/settings.yaml` via `GET /files?source=app&path=settings.yaml`
   (keeps the `etag`).
2. On install/uninstall, the board edits `INSTALLED_APPS` in the YAML
   (comment-preserving, client-side) and `PUT`s it back with the `etag`.
3. Board calls the existing `/stack/build` (+ restart). On success the Django
   `platform.Addon` reflection updates on the next boot; on failure the old
   runtime keeps serving and the board shows the build error.

## Open questions for the implementer

1. **Write allowlist vs any-file-in-source?** Broad (any file under the source) is
   simplest and matches "file tools in the workspace"; an allowlist (declared in
   the manifest) is safer for an internet-exposed daemon. Recommend: broad read,
   manifest-allowlisted write — but your call given the operator's threat model.
2. **etag/concurrency:** content-hash etag with `409` on mismatch (proposed) vs
   last-write-wins. Recommend the etag — two console tabs shouldn't clobber.
3. **List/delete:** include `GET /files/list` and `DELETE /files` now, or defer
   until a consumer needs them? The marketplace needs only read + write.
4. **Binary/size limits:** cap file size and treat content as UTF-8 text (config
   files); reject binary/oversize.
