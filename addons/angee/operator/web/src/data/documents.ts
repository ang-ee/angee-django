// Operations against the operator daemon. Field selections are validated against
// the daemon's own SDL (schema/operator.graphql, refreshed by the addon over its
// authenticated connection); the generated types in __generated__/operator.ts
// describe their results.

// The Django console field that hands the browser the daemon endpoint + a
// short-lived scoped token. This one targets Django's console schema, not the
// daemon.
export const OPERATOR_CONNECTION_QUERY = `
  query OperatorConnection {
    operatorConnection {
      endpoint
      token
    }
  }
`;

// The per-root field selections the console reads — the single source of truth
// for "what the snapshot is" (the field body of each root). The polling
// `SNAPSHOT_QUERY` selects these roots directly (gated by `@include`); the live
// `STACK_SNAPSHOT_SUBSCRIPTION` selects the same bodies under the daemon's
// aggregate `StackSnapshot`. Keeping the bodies here once means the query and the
// subscription can never drift. Each entry is the GraphQL field name and its
// selection body; `snapshotRoot` assembles them (the `@include` directive, when
// present, sits between the name and the body, where GraphQL requires it).
const SNAPSHOT_ROOTS = {
  health: { field: "health", body: `{
    status
    name
    message
  }` },
  stackStatus: { field: "stackStatus", body: `{
    root
    name
  }` },
  services: { field: "services", body: `{
    name
    runtime
    status
    health
  }` },
  workspaces: { field: "workspaces", body: `{
    name
    path
    template
    processComposePort
    ttl
    ttlExpiresAt
  }` },
  sources: { field: "sources", body: `{
    name
    slot
    kind
    path
    exists
    state
    branch
    ref
    currentRef
    dirty
    ahead
    behind
    pushed
  }` },
  gitOpsTopology: { field: "gitOpsTopology", body: `{
    root
    name
    summary {
      sources
      workspaces
      worktrees
      clean
      dirty
      ahead
      behind
      diverged
      missing
      error
      unpushed
    }
    links {
      id
      source
      workspace
      slot
      kind
      state
      branch
      ref
      currentRef
      dirty
      ahead
      behind
      pushed
      error
    }
  }` },
  jobs: { field: "jobs", body: `{
    name
    runtime
  }` },
  templates: { field: "templates", body: `{
    ref
    kind
    name
    path
    inputs {
      name
      type
      required
      immutable
      generated
      default
    }
  }` },
  secrets: { field: "secrets", body: `{
    name
    declared
    hasValue
    required
    generated
    import
    envVar
  }` },
} as const;

type SnapshotRoot = (typeof SNAPSHOT_ROOTS)[keyof typeof SNAPSHOT_ROOTS];

/** A snapshot root field, optionally gated by an `@include(if: $var)` directive. */
function snapshotRoot(root: SnapshotRoot, include?: string): string {
  const directive = include ? ` @include(if: ${include})` : "";
  return `${root.field}${directive} ${root.body}`;
}

// One query selecting the daemon root each pane needs; `@include` keeps a pane's
// roots out of the request when it isn't shown. Used for the first paint (the
// daemon emits no snapshot on connect); the live subscription supersedes it.
export const SNAPSHOT_QUERY = `
  query OperatorSnapshot(
    $wantOverview: Boolean!
    $wantServices: Boolean!
    $wantWorkspaces: Boolean!
    $wantSources: Boolean!
    $wantGitOps: Boolean!
    $wantOperations: Boolean!
    $wantTemplates: Boolean!
    $wantSecrets: Boolean!
  ) {
    ${snapshotRoot(SNAPSHOT_ROOTS.health, "$wantOverview")}
    ${snapshotRoot(SNAPSHOT_ROOTS.stackStatus, "$wantOverview")}
    ${snapshotRoot(SNAPSHOT_ROOTS.services, "$wantServices")}
    ${snapshotRoot(SNAPSHOT_ROOTS.workspaces, "$wantWorkspaces")}
    ${snapshotRoot(SNAPSHOT_ROOTS.sources, "$wantSources")}
    ${snapshotRoot(SNAPSHOT_ROOTS.gitOpsTopology, "$wantGitOps")}
    ${snapshotRoot(SNAPSHOT_ROOTS.jobs, "$wantOperations")}
    ${snapshotRoot(SNAPSHOT_ROOTS.templates, "$wantTemplates")}
    ${snapshotRoot(SNAPSHOT_ROOTS.secrets, "$wantSecrets")}
  }
`;

// The live snapshot push. `onStackSnapshotChange` fires whenever the daemon's
// aggregate snapshot hash changes and carries the whole `StackSnapshot`. Unlike
// `SNAPSHOT_QUERY`, this deliberately drops the per-pane `@include` gating and
// selects every root: one variable-free document means urql dedupes the eight
// panes' hooks to a single upstream subscription. The trade-off is that every
// push ships all roots and re-renders every pane; acceptable because the daemon
// assembles one hashed local aggregate. If the slow roots (`sources`/git topology)
// make pushes heavy, gate the subscription daemon-side with `want*` variables
// mirroring the query. The daemon does not emit on connect, so `SNAPSHOT_QUERY`
// still owns first paint.
export const STACK_SNAPSHOT_SUBSCRIPTION = `
  subscription OperatorStackSnapshot {
    onStackSnapshotChange {
      ${snapshotRoot(SNAPSHOT_ROOTS.health)}
      ${snapshotRoot(SNAPSHOT_ROOTS.stackStatus)}
      ${snapshotRoot(SNAPSHOT_ROOTS.services)}
      ${snapshotRoot(SNAPSHOT_ROOTS.workspaces)}
      ${snapshotRoot(SNAPSHOT_ROOTS.sources)}
      ${snapshotRoot(SNAPSHOT_ROOTS.gitOpsTopology)}
      ${snapshotRoot(SNAPSHOT_ROOTS.jobs)}
      ${snapshotRoot(SNAPSHOT_ROOTS.templates)}
      ${snapshotRoot(SNAPSHOT_ROOTS.secrets)}
    }
  }
`;

// Most lifecycle actions return `MutationResult { status name message }`; the
// daemon signals failure as a GraphQL error, not an `ok:false` flag, so any
// returned payload is a success.
const MUTATION_RESULT = `{
  status
  name
  message
}`;

// Source-mutating actions return the refreshed `SourceState`.
const SOURCE_FIELDS = `{
  name
  slot
  kind
  state
  branch
  ref
  currentRef
  dirty
  ahead
  behind
  pushed
}`;

export const SERVICE_START_MUTATION = `
  mutation OperatorServiceStart($name: String!) {
    serviceStart(name: $name) ${MUTATION_RESULT}
  }
`;

export const SERVICE_STOP_MUTATION = `
  mutation OperatorServiceStop($name: String!) {
    serviceStop(name: $name) ${MUTATION_RESULT}
  }
`;

export const SERVICE_RESTART_MUTATION = `
  mutation OperatorServiceRestart($name: String!) {
    serviceRestart(name: $name) ${MUTATION_RESULT}
  }
`;

// `serviceDestroy` stops and removes a service from the stack, leaving the
// workspace it mounts intact — the escape hatch for an orphaned service entry
// (a later `serviceCreate` over the same name 409s until it is destroyed).
export const SERVICE_DESTROY_MUTATION = `
  mutation OperatorServiceDestroy($name: String!) {
    serviceDestroy(name: $name) ${MUTATION_RESULT}
  }
`;

export const SERVICE_UP_MUTATION = `
  mutation OperatorServiceUp($name: String!) {
    serviceUp(name: $name) ${MUTATION_RESULT}
  }
`;

// The recent log buffer (history) for first paint of the service detail logs.
export const SERVICE_LOGS_QUERY = `
  query OperatorServiceLogHistory($name: String!, $limit: Int) {
    serviceLogs(name: $name, limit: $limit)
  }
`;

// The live log tail (v0.6: this now actually streams line-by-line). Each emission
// is one log line; the detail view accumulates them past the history.
export const SERVICE_LOGS_SUBSCRIPTION = `
  subscription OperatorServiceLogStream($name: String!) {
    onServiceLogs(name: $name)
  }
`;

// The service's resolved endpoint (routed URL + internal host/port) for detail.
export const SERVICE_ENDPOINT_QUERY = `
  query OperatorServiceEndpoint($name: String!) {
    serviceEndpoint(name: $name) {
      routed
      url
      internalHost
      internalPort
    }
  }
`;

// Render a service template into an existing workspace. `input.template` is the
// daemon's own template ref (from the `templates` listing) — the daemon owns its
// format, so resolve it from there rather than constructing it.
export const SERVICE_CREATE_MUTATION = `
  mutation OperatorServiceCreate($input: ServiceCreateInput!) {
    serviceCreate(input: $input) {
      name
      runtime
      status
      health
    }
  }
`;

// Render a workspace template into a new worktree workspace. `input.template` is
// the daemon's own template ref (see SERVICE_CREATE_MUTATION).
export const WORKSPACE_CREATE_MUTATION = `
  mutation OperatorWorkspaceCreate($input: WorkspaceCreateInput!) {
    workspaceCreate(input: $input) {
      name
      path
      template
      processComposePort
      ttl
      ttlExpiresAt
    }
  }
`;

export const WORKSPACE_DESTROY_MUTATION = `
  mutation OperatorWorkspaceDestroy($name: String!, $purge: Boolean) {
    workspaceDestroy(name: $name, purge: $purge) ${MUTATION_RESULT}
  }
`;

// `workspaceSyncBase` re-syncs the workspace's sources from its base and returns
// the refreshed source list.
export const WORKSPACE_SYNC_BASE_MUTATION = `
  mutation OperatorWorkspaceSyncBase($name: String!, $method: String) {
    workspaceSyncBase(name: $name, method: $method) ${SOURCE_FIELDS}
  }
`;

// The recent log buffer (history) for first paint of the workspace detail logs.
export const WORKSPACE_LOGS_QUERY = `
  query OperatorWorkspaceLogHistory($name: String!, $limit: Int) {
    workspaceLogs(name: $name, limit: $limit)
  }
`;

// The live log tail. Each emission is one log line; the detail view accumulates
// them past the history (mirrors the service log stream).
export const WORKSPACE_LOGS_SUBSCRIPTION = `
  subscription OperatorWorkspaceLogStream($name: String!) {
    onWorkspaceLogs(name: $name)
  }
`;

export const SOURCE_FETCH_MUTATION = `
  mutation OperatorSourceFetch($name: String!) {
    sourceFetch(name: $name) ${SOURCE_FIELDS}
  }
`;

export const SOURCE_PULL_MUTATION = `
  mutation OperatorSourcePull($name: String!) {
    sourcePull(name: $name) ${SOURCE_FIELDS}
  }
`;

export const SOURCE_PUSH_MUTATION = `
  mutation OperatorSourcePush($name: String!, $ref: String) {
    sourcePush(name: $name, ref: $ref) ${SOURCE_FIELDS}
  }
`;

export const STACK_UP_MUTATION = `
  mutation OperatorStackUp($input: StackRuntimeInput) {
    stackUp(input: $input) ${MUTATION_RESULT}
  }
`;

export const STACK_DOWN_MUTATION = `
  mutation OperatorStackDown {
    stackDown ${MUTATION_RESULT}
  }
`;

export const STACK_BUILD_MUTATION = `
  mutation OperatorStackBuild($input: StackRuntimeInput) {
    stackBuild(input: $input) ${MUTATION_RESULT}
  }
`;

export const STACK_DESTROY_MUTATION = `
  mutation OperatorStackDestroy($purge: Boolean) {
    stackDestroy(purge: $purge) ${MUTATION_RESULT}
  }
`;

// `jobRun` returns the launched job id as a scalar string.
export const JOB_RUN_MUTATION = `
  mutation OperatorJobRun($name: String!, $inputs: [KeyValueInput!]) {
    jobRun(name: $name, inputs: $inputs)
  }
`;

// `secretSet` returns the updated `SecretRef`; the daemon has no secret scope.
export const SECRET_SET_MUTATION = `
  mutation OperatorSecretSet($name: String!, $value: String!) {
    secretSet(name: $name, value: $value) {
      name
      declared
      hasValue
    }
  }
`;

export const SECRET_DELETE_MUTATION = `
  mutation OperatorSecretDelete($name: String!) {
    secretDelete(name: $name) ${MUTATION_RESULT}
  }
`;
