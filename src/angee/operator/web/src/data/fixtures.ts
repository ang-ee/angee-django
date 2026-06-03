// Sample data built against the daemon's real SDL types (re-exported from
// `./types` → `__generated__/operator`). Every fixture constructs a value of
// the generated type, so stories and tests render the same shape the live
// transport produces. The aggregate `operatorSnapshot` stitches the per-pane
// roots into the frontend-only `OperatorSnapshot`.
import type {
  GitOpsLink,
  GitOpsSummary,
  GitOpsTopology,
  JobState,
  MutationResult,
  OperatorSnapshot,
  SecretRef,
  ServiceState,
  SourceState,
  TemplateDescriptor,
  WorkspaceRef,
} from "./types";

export const operatorServices = [
  {
    name: "django",
    runtime: "docker",
    status: "running",
    health: "healthy",
  },
  {
    name: "vite",
    runtime: "docker",
    status: "running",
    health: "healthy",
  },
  {
    name: "worker",
    runtime: "docker",
    status: "stopped",
    health: null,
  },
] satisfies readonly ServiceState[];

export const operatorJobs = [
  {
    name: "build",
    runtime: "docker",
  },
  {
    name: "migrate",
    runtime: "docker",
  },
  {
    name: "resources-load",
    runtime: "docker",
  },
] satisfies readonly JobState[];

export const operatorSources = [
  {
    name: "framework",
    slot: "base",
    kind: "git",
    path: ".angee/sources/framework",
    exists: true,
    state: "clean",
    branch: "main",
    ref: "main",
    currentRef: "8f24a9c",
    dirty: false,
    ahead: 0,
    behind: 0,
    pushed: true,
    upstream: "origin/main",
    unpushedReason: null,
    error: null,
    commits: null,
  },
  {
    name: "notes-product",
    slot: "product",
    kind: "git",
    path: ".angee/sources/notes-product",
    exists: true,
    state: "dirty",
    branch: "main",
    ref: "main",
    currentRef: "4470c12",
    dirty: true,
    ahead: 2,
    behind: 1,
    pushed: false,
    upstream: "origin/main",
    unpushedReason: "ahead of upstream",
    error: null,
    commits: null,
  },
] satisfies readonly SourceState[];

export const operatorWorkspaces = [
  {
    name: "notes-dev",
    path: ".angee/workspaces/notes-dev",
    template: "dev",
    processComposePort: 8080,
    playwrightMcpName: "notes-dev-playwright",
    playwrightMcpUrl: "http://localhost:9323/sse",
    ttl: "24h",
    ttlExpiresAt: "2026-06-03T15:42:00Z",
  },
  {
    name: "operator-console",
    path: ".angee/workspaces/operator-console",
    template: "dev",
    processComposePort: 8081,
    playwrightMcpName: null,
    playwrightMcpUrl: null,
    ttl: null,
    ttlExpiresAt: null,
  },
] satisfies readonly WorkspaceRef[];

export const operatorTemplates = [
  {
    ref: "dev",
    kind: "workspace",
    name: "Development workspace",
    path: "templates/workspaces/dev",
    inputs: [
      {
        name: "base_ref",
        type: "string",
        required: true,
        immutable: false,
        generated: false,
        default: "main",
      },
      {
        name: "token",
        type: "string",
        required: false,
        immutable: true,
        generated: true,
        default: null,
      },
    ],
  },
  {
    ref: "dev-stack",
    kind: "stack",
    name: "Development stack",
    path: "templates/stacks/dev",
    inputs: [],
  },
] satisfies readonly TemplateDescriptor[];

export const operatorSecrets = [
  {
    name: "ANGEE_OPERATOR_TOKEN",
    declared: true,
    hasValue: true,
    required: true,
    generated: true,
    import: null,
    envVar: "ANGEE_OPERATOR_TOKEN",
  },
  {
    name: "DATABASE_URL",
    declared: true,
    hasValue: false,
    required: true,
    generated: false,
    import: null,
    envVar: "DATABASE_URL",
  },
] satisfies readonly SecretRef[];

const gitOpsSummary = {
  sources: 2,
  workspaces: 2,
  worktrees: 2,
  clean: 1,
  dirty: 1,
  ahead: 1,
  behind: 1,
  diverged: 0,
  branchMismatch: 0,
  missing: 0,
  error: 0,
  unpushed: 1,
} satisfies GitOpsSummary;

const gitOpsLinks = [
  {
    id: "notes-dev:base",
    source: "framework",
    workspace: "notes-dev",
    slot: "base",
    kind: "git",
    state: "clean",
    branch: "workspace/notes-dev",
    ref: "main",
    currentRef: "8f24a9c",
    dirty: false,
    ahead: 0,
    behind: 0,
    pushed: true,
    exists: true,
    mode: "worktree",
    upstream: "origin/workspace/notes-dev",
    unpushedReason: null,
    error: null,
    path: ".angee/workspaces/notes-dev/sources/framework",
  },
  {
    id: "operator-console:product",
    source: "notes-product",
    workspace: "operator-console",
    slot: "product",
    kind: "git",
    state: "dirty",
    branch: "workspace/operator-console",
    ref: "main",
    currentRef: "4470c12",
    dirty: true,
    ahead: 2,
    behind: 1,
    pushed: false,
    exists: true,
    mode: "worktree",
    upstream: "origin/workspace/operator-console",
    unpushedReason: "ahead of upstream",
    error: null,
    path: ".angee/workspaces/operator-console/sources/notes-product",
  },
] satisfies readonly GitOpsLink[];

export const gitOpsTopology = {
  root: "/Users/dev/angee-django",
  name: "angee-django",
  summary: gitOpsSummary,
  links: gitOpsLinks,
  sources: operatorSources,
  workspaces: [],
} satisfies GitOpsTopology;

const operatorHealth = {
  status: "ok",
  name: "operator",
  message: "Operator daemon is reachable.",
} satisfies MutationResult;

export const operatorSnapshot: OperatorSnapshot = {
  health: operatorHealth,
  stack: {
    root: "/Users/dev/angee-django",
    name: "angee-django",
  },
  services: operatorServices,
  jobs: operatorJobs,
  sources: operatorSources,
  workspaces: operatorWorkspaces,
  templates: operatorTemplates,
  secrets: operatorSecrets,
  gitOps: gitOpsTopology,
};
