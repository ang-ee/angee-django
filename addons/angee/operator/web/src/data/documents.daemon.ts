// Operations against the operator daemon. Field selections are validated against
// the daemon's own SDL (`runtime/schemas/operator.graphql`) by the unified
// `angee-web-codegen` pass; this file is intentionally not named `documents.ts`,
// because the Django console codegen scans that filename for console operations.

import { graphql } from "@angee/gql/operator";

export const OperatorMutationResultFields = graphql(`
  fragment OperatorMutationResultFields on MutationResult {
    status
    name
    message
  }
`);

export const OperatorServiceStateFields = graphql(`
  fragment OperatorServiceStateFields on ServiceState {
    id
    name
    runtime
    status
    health
  }
`);

export const OperatorWorkspaceRefFields = graphql(`
  fragment OperatorWorkspaceRefFields on WorkspaceRef {
    id
    name
    path
    template
    processComposePort
    ttl
    ttlExpiresAt
  }
`);

export const OperatorWorkspaceSourceStatusFields = graphql(`
  fragment OperatorWorkspaceSourceStatusFields on WorkspaceSourceStatus {
    slot
    source
    kind
    mode
    branch
    ref
    subpath
    path
    exists
    state
    currentRef
    dirty
    upstream
    ahead
    behind
    pushed
    unpushedReason
    error
  }
`);

export const OperatorWorkspaceStatusFields = graphql(`
  fragment OperatorWorkspaceStatusFields on WorkspaceStatus {
    name
    path
    exists
    state
    error
    innerError
    template
    processComposePort
    ttl
    ttlExpiresAt
    sources {
      ...OperatorWorkspaceSourceStatusFields
    }
  }
`);

export const OperatorSourceStateFields = graphql(`
  fragment OperatorSourceStateFields on SourceState {
    id
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
  }
`);

export const OperatorGitOpsTopologyFields = graphql(`
  fragment OperatorGitOpsTopologyFields on GitOpsTopology {
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
      branchMismatch
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
      mode
      state
      branch
      ref
      path
      exists
      currentRef
      dirty
      upstream
      ahead
      behind
      pushed
      unpushedReason
      error
    }
  }
`);

export const OperatorJobStateFields = graphql(`
  fragment OperatorJobStateFields on JobState {
    id
    name
    runtime
  }
`);

export const OperatorTemplateDescriptorFields = graphql(`
  fragment OperatorTemplateDescriptorFields on TemplateDescriptor {
    id
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
      question
    }
  }
`);

export const OperatorSecretRefFields = graphql(`
  fragment OperatorSecretRefFields on SecretRef {
    id
    name
    declared
    hasValue
    required
    generated
    import
    envVar
  }
`);

export const OperatorStackStatusFields = graphql(`
  fragment OperatorStackStatusFields on StackStatus {
    root
    name
  }
`);

// One query selecting the daemon root each pane needs; `@include` keeps a pane's
// roots out of the request when it isn't shown. Used for the first paint (the
// daemon emits no snapshot on connect); the live subscription supersedes it.
export const SNAPSHOT_QUERY = graphql(`
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
    health @include(if: $wantOverview) {
      ...OperatorMutationResultFields
    }
    stackStatus @include(if: $wantOverview) {
      ...OperatorStackStatusFields
    }
    services @include(if: $wantServices) {
      ...OperatorServiceStateFields
    }
    workspaces @include(if: $wantWorkspaces) {
      ...OperatorWorkspaceRefFields
    }
    sources @include(if: $wantSources) {
      ...OperatorSourceStateFields
    }
    gitOpsTopology @include(if: $wantGitOps) {
      ...OperatorGitOpsTopologyFields
    }
    jobs @include(if: $wantOperations) {
      ...OperatorJobStateFields
    }
    templates @include(if: $wantTemplates) {
      ...OperatorTemplateDescriptorFields
    }
    secrets @include(if: $wantSecrets) {
      ...OperatorSecretRefFields
    }
  }
`);

// The live snapshot push. `onStackSnapshotChange` fires whenever the daemon's
// aggregate snapshot hash changes and carries the whole `StackSnapshot`. Unlike
// `SNAPSHOT_QUERY`, this deliberately drops the per-pane `@include` gating and
// selects every root: one variable-free document so the active pane's hook needs
// no per-section variables to receive the live snapshot.
export const STACK_SNAPSHOT_SUBSCRIPTION = graphql(`
  subscription OperatorStackSnapshot {
    onStackSnapshotChange {
      health {
        ...OperatorMutationResultFields
      }
      stackStatus {
        ...OperatorStackStatusFields
      }
      services {
        ...OperatorServiceStateFields
      }
      workspaces {
        ...OperatorWorkspaceRefFields
      }
      sources {
        ...OperatorSourceStateFields
      }
      gitOpsTopology {
        ...OperatorGitOpsTopologyFields
      }
      jobs {
        ...OperatorJobStateFields
      }
      templates {
        ...OperatorTemplateDescriptorFields
      }
      secrets {
        ...OperatorSecretRefFields
      }
    }
  }
`);

export const SERVICE_START_MUTATION = graphql(`
  mutation OperatorServiceStart($name: String!) {
    serviceStart(name: $name) {
      ...OperatorMutationResultFields
    }
  }
`);

export const SERVICE_STOP_MUTATION = graphql(`
  mutation OperatorServiceStop($name: String!) {
    serviceStop(name: $name) {
      ...OperatorMutationResultFields
    }
  }
`);

export const SERVICE_RESTART_MUTATION = graphql(`
  mutation OperatorServiceRestart($name: String!) {
    serviceRestart(name: $name) {
      ...OperatorMutationResultFields
    }
  }
`);

// Delete the service resource through the daemon's Hasura-shaped root. The
// daemon owns the side effects (stop/remove) behind that resource mutation.
export const SERVICE_DESTROY_MUTATION = graphql(`
  mutation OperatorServiceDestroy($id: String!) {
    delete_services_by_pk(id: $id) {
      ...OperatorServiceStateFields
    }
  }
`);

export const SERVICE_UP_MUTATION = graphql(`
  mutation OperatorServiceUp($name: String!) {
    serviceUp(name: $name) {
      ...OperatorMutationResultFields
    }
  }
`);

// Service logs stream over the structured `/services/{name}/logs/stream`
// WebSocket (see useServiceLogStream), not GraphQL — exact per-service framing.

// The service's resolved endpoint (routed URL + internal host/port) for detail.
export const SERVICE_ENDPOINT_QUERY = graphql(`
  query OperatorServiceEndpoint($name: String!) {
    serviceEndpoint(name: $name) {
      routed
      url
      internalHost
      internalPort
    }
  }
`);

// Render a service template into an existing workspace. `object.template` is the
// daemon's own template ref (from the `templates` listing) — the daemon owns its
// format, so resolve it from there rather than constructing it.
export const SERVICE_CREATE_MUTATION = graphql(`
  mutation OperatorServiceCreate($object: services_insert_input!) {
    insert_services_one(object: $object) {
      ...OperatorServiceStateFields
    }
  }
`);

// Render a workspace template into a new worktree workspace. `object.template` is
// the daemon's own template ref (see SERVICE_CREATE_MUTATION).
export const WORKSPACE_CREATE_MUTATION = graphql(`
  mutation OperatorWorkspaceCreate($object: workspaces_insert_input!) {
    insert_workspaces_one(object: $object) {
      ...OperatorWorkspaceRefFields
    }
  }
`);

export const WORKSPACE_DESTROY_MUTATION = graphql(`
  mutation OperatorWorkspaceDestroy($id: String!) {
    delete_workspaces_by_pk(id: $id) {
      ...OperatorWorkspaceRefFields
    }
  }
`);

// `workspaceSyncBase` re-syncs the workspace's sources from its base and returns
// the refreshed source list.
export const WORKSPACE_SYNC_BASE_MUTATION = graphql(`
  mutation OperatorWorkspaceSyncBase($name: String!, $method: String) {
    workspaceSyncBase(name: $name, method: $method) {
      ...OperatorSourceStateFields
    }
  }
`);

// The recent log buffer (history) for first paint of the workspace detail logs.
export const WORKSPACE_LOGS_QUERY = graphql(`
  query OperatorWorkspaceLogHistory($name: String!, $limit: Int) {
    workspaceLogs(name: $name, limit: $limit)
  }
`);

// The live log tail. Each emission is one log line; the detail view accumulates
// them past the history (mirrors the service log stream).
export const WORKSPACE_LOGS_SUBSCRIPTION = graphql(`
  subscription OperatorWorkspaceLogStream($name: String!) {
    onWorkspaceLogs(name: $name)
  }
`);

export const WORKSPACE_STATUS_SUBSCRIPTION = graphql(`
  subscription OperatorWorkspaceStatus($name: String!) {
    onWorkspaceStatusChange(name: $name) {
      ...OperatorWorkspaceStatusFields
    }
  }
`);

export const SOURCE_FETCH_MUTATION = graphql(`
  mutation OperatorSourceFetch($name: String!) {
    sourceFetch(name: $name) {
      ...OperatorSourceStateFields
    }
  }
`);

export const SOURCE_PULL_MUTATION = graphql(`
  mutation OperatorSourcePull($name: String!) {
    sourcePull(name: $name) {
      ...OperatorSourceStateFields
    }
  }
`);

export const SOURCE_PUSH_MUTATION = graphql(`
  mutation OperatorSourcePush($name: String!, $ref: String) {
    sourcePush(name: $name, ref: $ref) {
      ...OperatorSourceStateFields
    }
  }
`);

export const STACK_UP_MUTATION = graphql(`
  mutation OperatorStackUp($input: StackRuntimeInput) {
    stackUp(input: $input) {
      ...OperatorMutationResultFields
    }
  }
`);

export const STACK_DOWN_MUTATION = graphql(`
  mutation OperatorStackDown {
    stackDown {
      ...OperatorMutationResultFields
    }
  }
`);

export const STACK_BUILD_MUTATION = graphql(`
  mutation OperatorStackBuild($input: StackRuntimeInput) {
    stackBuild(input: $input) {
      ...OperatorMutationResultFields
    }
  }
`);

export const STACK_DESTROY_MUTATION = graphql(`
  mutation OperatorStackDestroy($purge: Boolean) {
    stackDestroy(purge: $purge) {
      ...OperatorMutationResultFields
    }
  }
`);

// `jobRun` returns the launched job id as a scalar string.
export const JOB_RUN_MUTATION = graphql(`
  mutation OperatorJobRun($name: String!, $inputs: [KeyValueInput!]) {
    jobRun(name: $name, inputs: $inputs)
  }
`);

// Set a secret through the daemon's Hasura-shaped insert root; the daemon owns
// the write semantics for declared and ad hoc secrets.
export const SECRET_SET_MUTATION = graphql(`
  mutation OperatorSecretSet($object: secrets_insert_input!) {
    insert_secrets_one(object: $object) {
      ...OperatorSecretRefFields
    }
  }
`);

export const SECRET_DELETE_MUTATION = graphql(`
  mutation OperatorSecretDelete($id: String!) {
    delete_secrets_by_pk(id: $id) {
      ...OperatorSecretRefFields
    }
  }
`);
