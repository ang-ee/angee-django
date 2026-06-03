// Domain types come from the daemon's own SDL via codegen
// (`schema/operator.graphql` → `__generated__/operator.ts`); the console never
// hand-maintains them. This module re-exports the ones the panes render and adds
// the frontend-only `OperatorSnapshot`: the daemon exposes its state as separate
// root query fields, not one object, so the console aggregates the roots each
// pane needs into a single snapshot.
export type {
  ServiceState,
  JobState,
  SourceState,
  WorkspaceRef,
  TemplateDescriptor,
  TemplateInputDescriptor,
  SecretRef,
  StackStatus,
  GitOpsTopology,
  GitOpsLink,
  GitOpsSummary,
  CommitRef,
  MutationResult,
} from "../__generated__/operator";

import type {
  ServiceState,
  JobState,
  SourceState,
  WorkspaceRef,
  TemplateDescriptor,
  SecretRef,
  StackStatus,
  GitOpsTopology,
  MutationResult,
} from "../__generated__/operator";

export interface OperatorConnectionInfo {
  endpoint: string;
  token: string;
}

/**
 * The console snapshot: one daemon query selecting the root fields each pane
 * needs (the daemon has no single snapshot object). `health` is the daemon's
 * `MutationResult` health probe; `stack` carries the stack identity.
 */
export interface OperatorSnapshot {
  health: MutationResult | null;
  stack: Pick<StackStatus, "root" | "name"> | null;
  services: readonly ServiceState[];
  jobs: readonly JobState[];
  sources: readonly SourceState[];
  workspaces: readonly WorkspaceRef[];
  templates: readonly TemplateDescriptor[];
  secrets: readonly SecretRef[];
  gitOps: GitOpsTopology | null;
}

/** Raw snapshot-query result: each root is omitted unless its `@include` is on. */
export interface OperatorSnapshotQueryData {
  health?: MutationResult | null;
  stackStatus?: Pick<StackStatus, "root" | "name"> | null;
  services?: readonly ServiceState[] | null;
  jobs?: readonly JobState[] | null;
  sources?: readonly SourceState[] | null;
  workspaces?: readonly WorkspaceRef[] | null;
  templates?: readonly TemplateDescriptor[] | null;
  secrets?: readonly SecretRef[] | null;
  gitOpsTopology?: GitOpsTopology | null;
}

/** Which panes' data to fetch — maps to the snapshot query's `@include` flags. */
export interface OperatorSnapshotSections {
  overview?: boolean;
  services?: boolean;
  workspaces?: boolean;
  sources?: boolean;
  gitOps?: boolean;
  operations?: boolean;
  templates?: boolean;
  secrets?: boolean;
}
