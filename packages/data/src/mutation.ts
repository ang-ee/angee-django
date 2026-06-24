import { useCallback, useMemo } from "react";
import {
  useCreate,
  useCustomMutation,
  useInvalidate,
  useUpdate,
  type BaseKey,
  type BaseRecord,
  type HttpError,
} from "@refinedev/core";
import {
  useModelMetadata,
} from "@angee/sdk";

import {
  deletePreviewRequest,
  extractDeletePreview,
  type DeletePreview,
  type DeletePreviewVariables,
} from "./operations";
import { errorFromUnknown } from "./errors";
import { refineFieldsFromPaths } from "./list";
import type { Row } from "./rows";
import type { ResourceTypeName } from "./resource-types";
import { refineResourceName } from "./resources";

export type MutationAction = "create" | "update" | "delete";

export interface ResourceMutationVariables {
  /** For `create`/`update`: the values to insert or patch. */
  data?: Record<string, unknown>;
  /** For `delete`: the public id to remove. */
  id?: string;
  /** For `delete`: false previews the cascade without deleting. */
  confirm?: boolean;
}

export type ResourceMutationResult<TAction extends MutationAction = MutationAction> =
  TAction extends "delete" ? DeletePreview | null : Row | null;

export type ResourceMutate<TAction extends MutationAction = MutationAction> = (
  variables: ResourceMutationVariables,
) => Promise<ResourceMutationResult<TAction>>;

export interface UseResourceMutationState {
  fetching: boolean;
  error: Error | null;
}

export interface UseResourceMutationOptions {
  fields?: readonly string[];
  enabled?: boolean;
}

export function useResourceMutation<
  TName extends ResourceTypeName = ResourceTypeName,
  TAction extends MutationAction = MutationAction,
>(
  modelLabel: TName,
  action: TAction,
  options: UseResourceMutationOptions = {},
): [ResourceMutate<TAction>, UseResourceMutationState] {
  const { fields = [], enabled = true } = options;
  const metadata = useModelMetadata(modelLabel);
  const resource = metadata?.resource ?? null;
  const resourceName = resource ? refineResourceName(resource) : "";
  const dataProviderName = resource?.schemaName;
  const refineFields = useMemo(() => refineFieldsFromPaths(fields), [fields]);
  const create = useCreate<RowRecord, HttpError, Record<string, unknown>>({
    resource: resourceName,
    dataProviderName,
    meta: { fields: refineFields },
    invalidates: ["list", "many"],
  });
  const update = useUpdate<RowRecord, HttpError, Record<string, unknown>>({
    resource: resourceName,
    dataProviderName,
    meta: { fields: refineFields },
    invalidates: ["list", "many", "detail"],
  });
  const deletePreview = useCustomMutation<BaseRecord, HttpError, DeletePreviewVariables>();
  const invalidate = useInvalidate();

  const mutate = useCallback<ResourceMutate<TAction>>(
    async (variables) => {
      if (!enabled) {
        throw new Error(`Resource mutation for "${modelLabel}" is disabled.`);
      }
      if (!resource) {
        throw new Error(`Resource metadata for "${modelLabel}" is not available.`);
      }
      if (action === "create") {
        const response = await create.mutateAsync({
          values: dataVariables(variables),
        });
        return (response.data ?? null) as ResourceMutationResult<TAction>;
      }
      if (action === "update") {
        const { id, patch } = updateVariables(variables);
        const response = await update.mutateAsync({
          id,
          values: patch,
        });
        return (response.data ?? null) as ResourceMutationResult<TAction>;
      }

      const deleteInput = deleteVariables(variables);
      const request = deletePreviewRequest(resource, deleteInput);
      const response = await deletePreview.mutateAsync({
        url: "",
        method: "post",
        values: deleteInput,
        dataProviderName: request.dataProviderName,
        meta: request.meta,
      });
      if (variables.confirm === true) {
        await invalidate({
          resource: resourceName,
          dataProviderName: request.dataProviderName,
          id: variables.id,
          invalidates: ["list", "many", "detail"],
        });
      }
      return extractDeletePreview(response.data, request.root) as ResourceMutationResult<TAction>;
    },
    [
      action,
      create.mutateAsync,
      deletePreview.mutateAsync,
      enabled,
      invalidate,
      modelLabel,
      resource,
      resourceName,
      update.mutateAsync,
    ],
  );

  return [
    mutate,
    {
      fetching:
        create.mutation.isPending ||
        update.mutation.isPending ||
        deletePreview.mutation.isPending,
      error: errorFromUnknown(
        create.mutation.error ?? update.mutation.error ?? deletePreview.mutation.error,
      ),
    },
  ];
}

type RowRecord = BaseRecord & Row;

function dataVariables(variables: ResourceMutationVariables): Record<string, unknown> {
  if (!variables.data) {
    throw new Error("Resource create mutation requires data.");
  }
  return variables.data;
}

function updateVariables(
  variables: ResourceMutationVariables,
): { id: BaseKey; patch: Record<string, unknown> } {
  const data = dataVariables(variables);
  const id = data.id;
  if (typeof id !== "string" && typeof id !== "number") {
    throw new Error("Resource update mutation requires data.id.");
  }
  const { id: _id, ...patch } = data;
  return { id, patch };
}

function deleteVariables(variables: ResourceMutationVariables): DeletePreviewVariables {
  if (!variables.id) {
    throw new Error("Resource delete mutation requires id.");
  }
  return {
    id: variables.id,
    confirm: variables.confirm,
  };
}
