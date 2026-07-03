import type { AccessControlProvider, ResourceProps } from "@refinedev/core";

import type { DataResourceMetadata } from "./metadata";

const REFINE_ACTION_CAPABILITY: Readonly<Record<string, string>> = {
  list: "list",
  show: "detail",
  create: "create",
  edit: "update",
  delete: "delete",
  deleteMany: "delete",
  clone: "create",
};

interface ResourceWithDataMetadata extends ResourceProps {
  meta?: ResourceProps["meta"] & {
    resource?: DataResourceMetadata;
  };
}

/**
 * Build Refine access control from Angee's backend resource artifact.
 *
 * The provider is a UX gate, not the authorization boundary. The server still
 * enforces row and action permissions; the frontend only hides standard Refine
 * actions whose owning resource metadata does not expose a matching root.
 */
export function createAngeeAccessControlProvider(
  resources: readonly ResourceProps[],
): AccessControlProvider {
  const resourcesByKey = new Map<string, ResourceWithDataMetadata>();
  for (const resource of resources as readonly ResourceWithDataMetadata[]) {
    resourcesByKey.set(resource.name, resource);
    if (resource.identifier) resourcesByKey.set(resource.identifier, resource);
  }

  return {
    can: async ({ resource, action, params }) => {
      const item = resourceItemFor(resourcesByKey, resource, params?.resource);
      const metadata = item?.meta?.resource;
      if (!metadata) return { can: true };

      const capability = capabilityForRefineAction(action);
      const can = metadata.capabilities.includes(capability);
      return can
        ? { can: true }
        : {
            can: false,
            reason: `Resource "${metadata.modelLabel}" does not expose ${capability}.`,
          };
    },
    options: {
      buttons: {
        enableAccessControl: true,
        hideIfUnauthorized: true,
      },
    },
  };
}

export function capabilityForRefineAction(action: string): string {
  return REFINE_ACTION_CAPABILITY[action] ?? action;
}

function resourceItemFor(
  resourcesByKey: ReadonlyMap<string, ResourceWithDataMetadata>,
  resourceName: string | undefined,
  paramResource: ResourceProps | undefined,
): ResourceWithDataMetadata | undefined {
  if (paramResource?.identifier) {
    const item = resourcesByKey.get(paramResource.identifier);
    if (item) return item;
  }
  if (paramResource?.name) {
    const item = resourcesByKey.get(paramResource.name);
    if (item) return item;
  }
  return resourceName ? resourcesByKey.get(resourceName) : undefined;
}
