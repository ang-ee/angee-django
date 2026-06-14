// Provisioning primitives owned by the operator runtime: the daemon's template
// ref + answer-list shapes (it owns those formats) and typed create/destroy
// hooks over `useOperatorAction`. A consumer (e.g. the agents console) composes
// these to render something into the daemon without decoding the daemon itself.

import {
  SERVICE_CREATE_MUTATION,
  WORKSPACE_CREATE_MUTATION,
  WORKSPACE_DESTROY_MUTATION,
} from "./documents";
import { useOperatorAction, type OperatorActionHook } from "./transport";
import type {
  KeyValueInput,
  ServiceCreateInput,
  ServiceState,
  TemplateDescriptor,
  WorkspaceCreateInput,
  WorkspaceRef,
} from "./types";

/** A daemon template identified by its name + kind (e.g. an agent's template FK). */
export interface TemplateMatch {
  name?: string | null;
  kind?: string | null;
}

/**
 * Resolve the daemon's `template` ref for a name + kind. The daemon owns the ref
 * format and emits it in its own `templates` listing, so match against the
 * listing rather than constructing the ref. Matching is by `name` (+ optional
 * `kind`) because the descriptor's `path` is an absolute fs path on the daemon,
 * not the source-relative path Django records.
 */
export function resolveTemplateRef(
  templates: readonly TemplateDescriptor[],
  match: TemplateMatch | null | undefined,
): string | null {
  if (!match?.name) return null;
  const found = templates.find(
    (template) => template.name === match.name && (!match.kind || template.kind === match.kind),
  );
  return found?.ref ?? null;
}

/** Flatten an inputs object into the daemon's `[{key,value}]` answer list. */
export function toAnswerList(inputs: unknown): KeyValueInput[] {
  if (!inputs || typeof inputs !== "object") return [];
  return Object.entries(inputs as Record<string, unknown>).map(([key, value]) => ({
    key,
    value: typeof value === "string" ? value : JSON.stringify(value),
  }));
}

interface WorkspaceCreateVars extends Record<string, unknown> {
  input: WorkspaceCreateInput;
}
interface ServiceCreateVars extends Record<string, unknown> {
  input: ServiceCreateInput;
}
interface WorkspaceDestroyVars extends Record<string, unknown> {
  name: string;
  purge: boolean;
}

/** Render a workspace template into a new worktree workspace. */
export function useWorkspaceCreate(): OperatorActionHook<
  { workspaceCreate: WorkspaceRef | null },
  WorkspaceCreateVars
> {
  return useOperatorAction(WORKSPACE_CREATE_MUTATION);
}

/** Render a service template into an existing workspace. */
export function useServiceCreate(): OperatorActionHook<
  { serviceCreate: ServiceState | null },
  ServiceCreateVars
> {
  return useOperatorAction(SERVICE_CREATE_MUTATION);
}

/** Tear a workspace (and its services) down. */
export function useWorkspaceDestroy(): OperatorActionHook<
  Record<string, unknown>,
  WorkspaceDestroyVars
> {
  return useOperatorAction(WORKSPACE_DESTROY_MUTATION);
}
