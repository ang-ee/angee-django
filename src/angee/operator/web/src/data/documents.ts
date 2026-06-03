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

// One query selecting the daemon root each pane needs; `@include` keeps a pane's
// roots out of the request when it isn't shown.
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
    health @include(if: $wantOverview) {
      status
      name
      message
    }
    stackStatus @include(if: $wantOverview) {
      root
      name
    }
    services @include(if: $wantServices) {
      name
      runtime
      status
      health
    }
    workspaces @include(if: $wantWorkspaces) {
      name
      path
      template
      processComposePort
      ttl
      ttlExpiresAt
    }
    sources @include(if: $wantSources) {
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
    gitOpsTopology @include(if: $wantGitOps) {
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
    }
    jobs @include(if: $wantOperations) {
      name
      runtime
    }
    templates @include(if: $wantTemplates) {
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
    }
    secrets @include(if: $wantSecrets) {
      name
      declared
      hasValue
      required
      generated
      import
      envVar
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

export const SERVICE_UP_MUTATION = `
  mutation OperatorServiceUp($name: String!) {
    serviceUp(name: $name) ${MUTATION_RESULT}
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
