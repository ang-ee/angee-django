import type {
  AngeeSchemaMetadata,
  DataResourceMetadata,
  SchemaFieldMetadata,
} from "./artifact";
import type { ResourceProps } from "@refinedev/core";
import type { ReactNode } from "react";
import { dataResourcesFromAngeeSchemaMetadata } from "./projection";

export interface AngeeRefineResource extends ResourceProps {
  name: string;
  identifier: string;
  meta: RefineResourceMetadata & {
    dataProviderName: string;
    modelLabel: string;
    schemaName: string;
    resource: DataResourceMetadata;
  };
}

export interface RefineResourceOptions {
  pathsByResource?: Readonly<Record<string, string>>;
  metadataByResource?: Readonly<Record<string, RefineResourceMetadata>>;
}

export interface RefineResourceMetadata {
  label?: string;
  icon?: ReactNode;
  parent?: string;
  hide?: boolean;
  menuId?: string;
  description?: string;
  group?: string;
  sidebar?: boolean;
  status?: string;
  tone?: string;
  badge?: number;
}

export function refineResourcesFromSchemaMetadata(
  metadata: SchemaFieldMetadata,
  options: RefineResourceOptions = {},
): readonly AngeeRefineResource[] {
  return refineResourcesFromDataResources(metadata.resources ?? [], options);
}

export function refineResourcesFromAngeeSchemaMetadata(
  metadata: AngeeSchemaMetadata | undefined,
  options: RefineResourceOptions = {},
): readonly AngeeRefineResource[] {
  return refineResourcesFromDataResources(
    dataResourcesFromAngeeSchemaMetadata(metadata),
    options,
  );
}

export function refineResourcesFromDataResources(
  resources: readonly DataResourceMetadata[],
  options: RefineResourceOptions = {},
): readonly AngeeRefineResource[] {
  return resources
    .filter((resource) => resource.roots.list)
    .map((resource) => refineResourceFromDataResource(resource, options));
}

export function refineResourceName(resource: DataResourceMetadata): string {
  return requiredRoot(resource, "list");
}

/**
 * The refine resource identifier (`<schema>:<modelLabel>`) a resource registers
 * under — the stable key refine builds list query keys and invalidations on.
 * Hand-built data hooks (e.g. a batched `useQueries` list fetch) key on this so
 * `useInvalidate` and live `changes()` events reach them exactly as they reach a
 * `useList`.
 */
export function refineResourceIdentifier(resource: DataResourceMetadata): string {
  return `${resource.schemaName}:${resource.modelLabel}`;
}

function refineResourceFromDataResource(
  resource: DataResourceMetadata,
  options: RefineResourceOptions,
): AngeeRefineResource {
  const route =
    options.pathsByResource?.[resource.modelLabel]
    ?? options.pathsByResource?.[resource.modelName];
  const metadata =
    options.metadataByResource?.[resource.modelLabel]
    ?? options.metadataByResource?.[resource.modelName]
    ?? {};
  return {
    name: refineResourceName(resource),
    identifier: refineResourceIdentifier(resource),
    meta: {
      hide: metadata.hide ?? true,
      ...metadata,
      dataProviderName: resource.schemaName,
      modelLabel: resource.modelLabel,
      schemaName: resource.schemaName,
      resource,
    },
    ...(route ? routeActions(route, resource) : {}),
  };
}

function routeActions(
  route: string,
  resource: DataResourceMetadata,
): Pick<AngeeRefineResource, "list" | "show" | "create" | "edit"> {
  const normalized = refineRoutePathForTanStack(
    route === "/" ? "" : route.replace(/\/+$/, ""),
  );
  return {
    list: normalized || "/",
    ...(resource.roots.detail ? { show: `${normalized}/:id` } : {}),
    ...(resource.roots.create ? { create: `${normalized}/new` } : {}),
    ...(resource.roots.update ? { edit: `${normalized}/:id` } : {}),
  };
}

export function refineRoutePathForTanStack(path: string): string {
  return path.replace(/(^|\/)\$([^/?#]+)/g, "$1:$2");
}

function requiredRoot(
  resource: DataResourceMetadata,
  root: keyof DataResourceMetadata["roots"],
): string {
  const value = resource.roots[root];
  if (!value) {
    throw new Error(
      `Resource "${resource.modelLabel}" does not declare a ${root} root.`,
    );
  }
  return value;
}
