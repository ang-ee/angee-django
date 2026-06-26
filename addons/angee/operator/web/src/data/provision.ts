// Provisioning primitives owned by the operator runtime: the daemon's template
// ref + answer-list shapes (it owns those formats) and typed Hasura resource
// hooks over `useOperatorAction`. A consumer (e.g. the agents console) composes
// these to render something into the daemon without decoding the daemon itself.

import {
  SERVICE_CREATE_MUTATION,
  WORKSPACE_CREATE_MUTATION,
  WORKSPACE_DESTROY_MUTATION,
  WORKSPACE_STATUS_SUBSCRIPTION,
} from "./documents.daemon";
import { useMemo } from "react";
import type { DocumentData } from "@angee/refine";

import {
  useOperatorAction,
  useOperatorSubscription,
  type OperatorActionHook,
} from "./transport";
import type {
  KeyValueInput,
  TemplateDescriptor,
} from "./types";

type WorkspaceStatusData = DocumentData<typeof WORKSPACE_STATUS_SUBSCRIPTION>;
export type ProvisionWorkspaceStatus = WorkspaceStatusData["onWorkspaceStatusChange"];

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

/** Render a workspace template through the daemon's workspace resource insert. */
export function useWorkspaceCreate(): OperatorActionHook<typeof WORKSPACE_CREATE_MUTATION> {
  return useOperatorAction(WORKSPACE_CREATE_MUTATION);
}

/** Render a service template through the daemon's service resource insert. */
export function useServiceCreate(): OperatorActionHook<typeof SERVICE_CREATE_MUTATION> {
  return useOperatorAction(SERVICE_CREATE_MUTATION);
}

/** Delete a workspace resource through the daemon. */
export function useWorkspaceDestroy(): OperatorActionHook<typeof WORKSPACE_DESTROY_MUTATION> {
  return useOperatorAction(WORKSPACE_DESTROY_MUTATION);
}

export interface WorkspaceStatusResult {
  status: ProvisionWorkspaceStatus | null;
  fetching: boolean;
  error: Error | null;
}

/** Subscribe to the daemon-owned live status for one workspace. */
export function useWorkspaceStatus(name: string): WorkspaceStatusResult {
  const variables = useMemo(() => ({ name }), [name]);
  const result = useOperatorSubscription(WORKSPACE_STATUS_SUBSCRIPTION, variables, {
    enabled: Boolean(name),
  });

  return {
    status: result.data?.onWorkspaceStatusChange ?? null,
    fetching: result.fetching,
    error: result.error,
  };
}
