import type { MetaQuery } from "@refinedev/core";
import { assertName as assertGraphQLName } from "graphql";

export function queryMeta(
  gqlQuery: unknown,
  gqlVariables: Record<string, unknown>,
): MetaQuery {
  return { gqlQuery, gqlVariables } as MetaQuery;
}

export function mutationMeta(
  gqlMutation: unknown,
  gqlVariables: Record<string, unknown>,
): MetaQuery {
  return { gqlMutation, gqlVariables } as MetaQuery;
}

export function operationName(name: string): string {
  return assertGraphQLName(name);
}

export function fieldRecord(
  data: unknown,
  field: string,
): Record<string, unknown> | null {
  return recordValue(recordValue(data)?.[field]);
}

export function recordValue(value: unknown): Record<string, unknown> | null {
  return isRecord(value) ? value : null;
}

export function arrayValue(value: unknown): readonly unknown[] {
  return Array.isArray(value) ? value : [];
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function countOf(value: unknown): number {
  return typeof value === "number" ? value : 0;
}

export function stringValue(value: unknown): string | null {
  if (value == null) return null;
  const text = String(value).trim();
  return text === "" ? null : text;
}

export function authoredOperationData<TData>(payload: unknown): TData | undefined {
  if (isGraphQLResponseEnvelope(payload)) {
    return payload.data as TData | undefined;
  }
  return payload as TData | undefined;
}

function isGraphQLResponseEnvelope(
  payload: unknown,
): payload is { data?: unknown; errors?: unknown; extensions?: unknown } {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    return false;
  }
  const keys = Object.keys(payload);
  return keys.includes("data") &&
    keys.every((key) => key === "data" || key === "errors" || key === "extensions");
}
