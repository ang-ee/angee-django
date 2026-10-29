// The daemon operation documents are validated against the daemon's own SDL by the
// unified codegen (`runtime/schemas/external/operator.graphql`, else the committed
// `web/schema/operator.graphql` → `@angee/gql/operator`). The shapes the panes render
// come from the fragments those documents select, never from the daemon's full
// generated types: a daemon release that adds a field, required or not, changes no
// pane until a fragment selects it. Only inputs the console sends keep the generated
// type. This module also adds the frontend-only `OperatorSnapshot`: the daemon
// exposes its state as separate root query fields, not one object, so the console
// aggregates the roots each pane needs into a single snapshot.
import type { DocumentData, DocumentVariables } from "@angee/refine";

import type {
  OperatorGitOpsTopologyFields,
  OperatorJobStateFields,
  OperatorMutationResultFields,
  OperatorSecretRefFields,
  OperatorServiceStateFields,
  OperatorSourceStateFields,
  OperatorStackStatusFields,
  OperatorTemplateDescriptorFields,
  OperatorWorkspaceRefFields,
  OperatorWorkspaceSourceStatusFields,
  OperatorWorkspaceStatusFields,
  SNAPSHOT_QUERY,
  STACK_SNAPSHOT_SUBSCRIPTION,
} from "./documents.daemon";

export type { WorkspaceCreateInput, KeyValueInput } from "@angee/gql/operator/types";

export type ServiceState = DocumentData<typeof OperatorServiceStateFields>;
export type JobState = DocumentData<typeof OperatorJobStateFields>;
export type SourceState = DocumentData<typeof OperatorSourceStateFields>;
export type WorkspaceRef = DocumentData<typeof OperatorWorkspaceRefFields>;
export type WorkspaceStatus = DocumentData<typeof OperatorWorkspaceStatusFields>;
export type WorkspaceSourceStatus = DocumentData<typeof OperatorWorkspaceSourceStatusFields>;
export type SecretRef = DocumentData<typeof OperatorSecretRefFields>;
export type StackStatus = DocumentData<typeof OperatorStackStatusFields>;
export type GitOpsTopology = DocumentData<typeof OperatorGitOpsTopologyFields>;
export type GitOpsLink = GitOpsTopology["links"][number];
export type GitOpsSummary = GitOpsTopology["summary"];
export type MutationResult = DocumentData<typeof OperatorMutationResultFields>;
export type TemplateDescriptor = DocumentData<typeof OperatorTemplateDescriptorFields>;
export type TemplateInputDescriptor = TemplateDescriptor["inputs"][number];

export interface OperatorConnectionInfo {
  endpoint: string;
  token: string;
  expiresAt: string;
  restartJob?: string | null;
}

/**
 * The console snapshot: one daemon query selecting the root fields each pane
 * needs (the daemon has no single snapshot object). `health` is the daemon's
 * `MutationResult` health probe; `stack` carries the stack identity.
 */
export interface OperatorSnapshot {
  health: MutationResult | null;
  stack: StackStatus | null;
  services: readonly ServiceState[];
  jobs: readonly JobState[];
  sources: readonly SourceState[];
  workspaces: readonly WorkspaceRef[];
  templates: readonly TemplateDescriptor[];
  secrets: readonly SecretRef[];
  gitOps: GitOpsTopology | null;
}

/** Raw snapshot-query result: each root is omitted unless its `@include` is on. */
export type OperatorSnapshotQueryData = DocumentData<typeof SNAPSHOT_QUERY>;

/** The snapshot query's `@include` toggles — one per pane (`$wantOverview`...). */
export type OperatorSnapshotQueryVariables =
  DocumentVariables<typeof SNAPSHOT_QUERY>;

/**
 * The live snapshot push: `onStackSnapshotChange` carries the daemon's aggregate
 * `StackSnapshot` (the generated type). It selects every root, so all are present
 * on each push; its non-null arrays are assignable to the query's nullable roots,
 * so {@link snapshotFromQueryData} consumes either source.
 */
export type OperatorSnapshotSubscriptionData = DocumentData<
  typeof STACK_SNAPSHOT_SUBSCRIPTION
>;

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
