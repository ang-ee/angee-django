import { useMemo } from "react";
import {
  useCustom,
  type BaseRecord,
  type HttpError,
} from "@refinedev/core";
import {
  useModelMetadata,
  resourceOperationTarget,
  type DataResourceOperationTarget,
} from "@angee/resources";

import {
  extractRevisions,
  revisionsRequest,
  type ResourceRevision,
} from "@angee/refine";
import {
  revisionDocumentForResource,
  useOperationDocuments,
} from "@angee/refine";
import { errorFromUnknown } from "./errors";
import type { ResourceTypeName } from "@angee/resources";

const INERT_REVISION_TARGET: DataResourceOperationTarget = {
  dataProviderName: "default",
  root: "__typename",
};
const INERT_REVISION_DOCUMENT = { kind: "Document", definitions: [] };

export interface UseResourceRevisionsOptions {
  enabled?: boolean;
}

export interface UseResourceRevisionsResult {
  revisions: readonly ResourceRevision[];
  count: number;
  fetching: boolean;
  error: Error | null;
  refetch: () => void;
}

export function useResourceRevisions<
  TName extends ResourceTypeName = ResourceTypeName,
>(
  modelLabel: TName,
  id: string | null | undefined,
  options: UseResourceRevisionsOptions = {},
): UseResourceRevisionsResult {
  const { enabled = true } = options;
  const metadata = useModelMetadata(modelLabel);
  const resource = metadata?.resource ?? null;
  const operationDocuments = useOperationDocuments();
  const active =
    enabled &&
    id !== null &&
    id !== undefined &&
    id !== "" &&
    resource !== null &&
    Boolean(resource.roots.revisions);
  const request = useMemo(
    () => {
      if (!active || !resource || !id) {
        return revisionsRequest(INERT_REVISION_TARGET, "", {
          document: INERT_REVISION_DOCUMENT,
        });
      }
      return revisionsRequest(
        resourceOperationTarget(resource, "revisions"),
        id,
        {
          document: revisionDocumentForResource(
            operationDocuments,
            resource.schemaName,
            resource.modelLabel,
          ),
        },
      );
    },
    [active, id, operationDocuments, resource],
  );
  const run = useCustom<BaseRecord, HttpError>({
    url: "",
    method: "post",
    dataProviderName: request.dataProviderName,
    meta: request.meta,
    queryOptions: { enabled: active },
  });
  const data = run.query.data?.data ?? run.result.data;
  const revisions = useMemo(
    () => extractRevisions(data, request.root),
    [data, request.root],
  );

  return {
    revisions,
    count: revisions.length,
    fetching: run.query.isFetching,
    error: errorFromUnknown(run.query.error),
    refetch: () => {
      void run.query.refetch();
    },
  };
}
