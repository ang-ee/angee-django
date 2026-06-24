import type {
  AngeeSchemaMetadata,
  DataResourceMetadata,
  SchemaFieldMetadata,
} from "@angee/sdk";
import type { ResourceProps } from "@refinedev/core";

export interface AngeeRefineResource extends ResourceProps {
  name: string;
  identifier: string;
  meta: {
    dataProviderName: string;
    modelLabel: string;
    schemaName: string;
    resource: DataResourceMetadata;
  };
}

export interface RefineResourceOptions {
  pathsByModel?: Readonly<Record<string, string>>;
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
  return refineResourcesFromDataResources(metadata?.angee?.resources ?? [], options);
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

function refineResourceFromDataResource(
  resource: DataResourceMetadata,
  options: RefineResourceOptions,
): AngeeRefineResource {
  const route =
    options.pathsByModel?.[resource.modelLabel]
    ?? options.pathsByModel?.[resource.modelName];
  return {
    name: refineResourceName(resource),
    identifier: `${resource.schemaName}:${resource.modelLabel}`,
    meta: {
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
  const normalized = route === "/" ? "" : route.replace(/\/+$/, "");
  return {
    list: normalized || "/",
    ...(resource.roots.detail ? { show: `${normalized}/:id` } : {}),
    ...(resource.roots.create ? { create: `${normalized}/new` } : {}),
    ...(resource.roots.update ? { edit: `${normalized}/:id` } : {}),
  };
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
