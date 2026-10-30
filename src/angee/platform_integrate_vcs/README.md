# Built-distribution catalogue sources

The catalogue consumes package metadata and already built distributions. Authors
and CI build and publish wheels. Catalogue refresh never installs a package,
imports addon code, executes a build backend, clones a repository, or invokes an
operator build job.

Platform already discovers installed distributions through `importlib.metadata`
and the native `django-compose` index. Installation from public or private
package indexes remains the existing uv/operator dependency workflow; the
catalogue reads the resulting installed metadata. Direct browsing of uninstalled
standard-index packages is not currently an addon source type. No public PyPI
publication or separate committed catalogue manifest is required. An artifact
source selects one current wheel per distribution; conflicting versions are
refused by the native identity index rather than resolved by catalogue policy.

An optional `Source(kind="addon")` indexes explicit wheel artifacts:

- A local VCS bridge reads `.whl` files beneath the Source's `path`, such as `dist`.
- A remote VCS bridge resolves the chosen `ref` once and reads committed or
  CI-produced `.whl` blobs beneath `path` at that immutable revision through its
  native authenticated binary file API. Source-only trees report the required
  author/CI build step and preserve the previous catalogue.

The wheel's METADATA, entry points, RECORD and packaged `pyproject.toml` are
read-only data. Native distribution metadata owns package/application identity;
packaged TOML contributes capabilities only. This includes dynamically generated
Hatch entry points: the catalogue reads their built result and duplicates no
backend naming rule. Ordinary library wheels do not create addon rows.

Both artifact paths use the same native index and Platform projection before
source CAS/reconciliation. Invalid, unavailable or changed inputs preserve
existing rows and `last_synced_at`. Installed metadata keeps precedence;
artifacts add its provenance. Other local VCS consumers retain their existing
working-tree semantics.

A source admits at most 128 wheel artifacts. Wheel files are capped at 64 MiB,
returned data files at 64 KiB, and their complete metadata snapshot at 4 MiB.
Native RECORD paths and ZIP member names must remain relative. Metadata uses
only standard stored/deflated wheel compression and bounded decompression.
Only data is copied into the temporary native metadata view; code and asset
inventories are not transported or extracted. Temporary data is removed after
projection, and no persistent metadata cache is maintained.
