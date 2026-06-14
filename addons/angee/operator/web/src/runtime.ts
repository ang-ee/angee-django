// The operator daemon runtime, shared with other addons. The package's default
// entry (".") stays the `BaseAddon` registration; this subpath exposes the
// transport, data hooks, and status widgets so an addon that provisions through
// the operator (e.g. `@angee/agents`) embeds the *same* workspace/service views
// instead of re-plumbing the daemon connection. Wrap any consumer in
// `OperatorTransportProvider` to supply the daemon client.

export {
  OperatorTransportProvider,
  useOperatorClient,
  useOperatorSnapshot,
  useOperatorAction,
} from "./data/transport";
export type {
  OperatorTransportProviderProps,
  OperatorSnapshotResult,
  OperatorActionHook,
} from "./data/transport";

export type {
  OperatorConnectionInfo,
  OperatorSnapshot,
  OperatorSnapshotQueryData,
  OperatorSnapshotSections,
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
} from "./data/types";

// Provisioning primitives: the daemon's template-ref + answer-list shapes (it
// owns those formats) and typed create/destroy hooks. A consumer composes these
// to render something into the daemon — the agents console provisions an agent.
export {
  resolveTemplateRef,
  toAnswerList,
  useWorkspaceCreate,
  useServiceCreate,
  useWorkspaceDestroy,
} from "./data/provision";
export type { TemplateMatch } from "./data/provision";

export { runDaemonAction } from "./views/parts/run-action";
export type { DaemonActionData, RunDaemonActionParams } from "./views/parts/run-action";

export { StateTag } from "./views/parts/StateTag";
export type { StateTagProps } from "./views/parts/StateTag";

export { OperatorSection } from "./views/parts/OperatorSection";
export type { OperatorSectionProps } from "./views/parts/OperatorSection";

export { ServicesSection } from "./views/sections/ServicesSection";
export type { ServicesSectionProps } from "./views/sections/ServicesSection";

export { WorkspacesSection } from "./views/sections/WorkspacesSection";
export type { WorkspacesSectionProps } from "./views/sections/WorkspacesSection";
