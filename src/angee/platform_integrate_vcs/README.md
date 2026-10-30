# VCS catalogue metadata

Remote catalogue preparation executes only in an operator `source-build` job.
The selected VCS connection resolves the immutable commit and refreshes its own
credential before handing that material to the fetch phase. The offline build
phase receives no credential or network access. Django receives bounded native
wheel metadata and packaged capability TOML, then uses `index_components` and
Platform's existing distribution readers before its source CAS/reconcile
transaction. A failed, unavailable or invalid operation preserves existing rows
and `last_synced_at`; inspect the native job receipt for the build diagnostic.

## Prepare the trusted build job

Build the supplied image administratively, before enabling source discovery:

```sh
docker build -t catalogue-metadata:prepared \
  src/angee/platform_integrate_vcs/metadata-image
docker image inspect catalogue-metadata:prepared --format '{{.Id}}'
```

The image's requirements are pinned with hashes. Preparing the image downloads
those exact wheels into `/wheelhouse` and installs the build frontend and
approved backends in `/opt/catalogue`. A catalogue operation installs nothing.
If a package requires another backend or build dependency, update and review
`metadata-image/requirements.in`, regenerate its native hash lock, and prepare a
new image. Missing dependencies fail rather than using the network or static
TOML identity. The checked-in image supports Hatch/hatch-angee and Flit.

Declare the job in the controlling stack's trusted `angee.yaml`; replace both
image placeholders with immutable image IDs or repository digests. The fetch
image must provide Python, Git and CA certificates as described by the native
operator's source-build contract. Never configure this job from catalogue rows
or package metadata.

```yaml
jobs:
  catalogue-metadata:
    runtime: container
    image: "sha256:<prepared-build-image-id>"
    command: [/opt/catalogue/bin/python, -I, /work/artifact, /work/repository]
    source_build:
      fetch_image: "sha256:<prepared-fetch-image-id>"
      timeout_seconds: 300
      memory_mb: 512
      pids_limit: 64
      storage_mb: 512
      output_bytes: 262144
```

`ANGEE_CATALOGUE_METADATA_JOB` defaults to `catalogue-metadata`. The operator owns
the separate credentialed fetch and offline, unprivileged build, their immutable
images, resource limits and cleanup. No checkout endpoint or shared Django/build
filesystem is involved. Source `path` scopes project discovery within the
pinned checkout; every discovered build project must produce valid metadata.
Choose a subtree when a repository contains intentionally invalid build fixtures.

The worker accepts at most 128 projects and 10,000 wheel entries. Wheel archives
are capped at 64 MiB, individual returned data files at 64 KiB, and the complete
JSON result at 256 KiB. Returned paths and native RECORD paths cannot escape
those data snapshots. RECORD retains only its original entries for returned
metadata/document files; code and asset inventories are not transported. The job's declared output limit must not exceed that
result limit. Local and remote catalogue inputs share these metadata limits.

## Local sources

Build wheels explicitly with the project's normal packaging workflow outside
Django, then set the local addon Source's `path` to their directory, for example
`dist`. Refresh indexes those `.whl` artifacts recursively using native METADATA,
entry points, RECORD and packaged `pyproject.toml`. It never imports their code,
runs a build hook, or calls the operator. An empty wheel directory reports the
required action and retains the prior catalogue. Other LocalVCSBackend consumers
continue to read uncommitted working-tree files through their existing contract.
