import {
  resourceOperationTarget,
  type Row,
} from "@angee/resources";
import {
  useCreate,
  useCustomMutation,
  useInvalidate,
  useUpdate,
  type BaseRecord,
  type HttpError,
  } from "@refinedev/core";
import {
  refineFieldsFromPaths,
  } from "@angee/refine";
import {
  deletePreviewDocumentForResource,
  deletePreviewRequest,
  extractDeletePreview,
  useOperationDocuments,
  type DeletePreviewVariables,
  } from "@angee/refine";
import {
  refineResourceName,
} from "@angee/resources";
import {
  rowPublicId,
} from "@angee/resources";
import {
  useBusyRun } from "@angee/ui";
import {
  useModelMetadata,
} from "@angee/resources";

export interface PageActions {
  busy: boolean;
  /** Create a page in a vault, optionally under a parent; returns its node id. */
  createPage: (input: {
    vault: string;
    title: string;
    kind: string;
    parent: string | null;
  }) => Promise<string | null>;
  /** Delete a page (and its subtree). */
  deletePage: (id: string) => Promise<void>;
  /** Reparent a page (move) — `null` lifts it to the vault root. */
  movePage: (id: string, parent: string | null) => Promise<void>;
}

/**
 * The navigator write verbs over the knowledge CRUD mutations (create/delete are
 * the gated factory mutations; move rides `updatePage`'s parent patch).
 * `onChanged` fires after each so the caller can refetch the tree.
 */
export function usePageActions(
  options: { onChanged?: () => void } = {},
): PageActions {
  const { onChanged } = options;
  const metadata = useModelMetadata(PAGE_MODEL);
  const resource = metadata?.resource ?? null;
  const operationDocuments = useOperationDocuments();
  const resourceName = resource ? refineResourceName(resource) : "";
  const fields = refineFieldsFromPaths(["id", "title"]);
  const createPageMutation = useCreate<RowRecord, HttpError, Record<string, unknown>>({
    resource: resourceName,
    dataProviderName: resource?.schemaName,
    meta: { fields },
    invalidates: ["list", "many"],
  });
  const updatePageMutation = useUpdate<RowRecord, HttpError, Record<string, unknown>>({
    resource: resourceName,
    dataProviderName: resource?.schemaName,
    meta: { fields },
    invalidates: ["list", "many", "detail"],
  });
  const deletePageMutation =
    useCustomMutation<BaseRecord, HttpError, DeletePreviewVariables>();
  const invalidate = useInvalidate();
  const { busy, run } = useBusyRun(onChanged);

  return {
    busy,
    createPage: ({ vault, title, kind, parent }) =>
      run(async () => {
        requirePageResource(resource);
        const response = await createPageMutation.mutateAsync({
          values: { vault, title, kind, parent },
        });
        return rowPublicId(response.data ?? null);
      }),
    deletePage: (id) =>
      run(async () => {
        requirePageResource(resource);
        const request = deletePreviewRequest(
          resourceOperationTarget(resource, "deletePreview"),
          { id, confirm: true },
          {
            document: deletePreviewDocumentForResource(
              operationDocuments,
              resource.schemaName,
              resource.modelLabel,
            ),
          },
        );
        const response = await deletePageMutation.mutateAsync({
          url: "",
          method: "post",
          values: { id, confirm: true },
          dataProviderName: request.dataProviderName,
          meta: request.meta,
        });
        void extractDeletePreview(response.data, request.root);
        await invalidate({
          resource: refineResourceName(resource),
          dataProviderName: request.dataProviderName,
          id,
          invalidates: ["list", "many", "detail"],
        });
      }),
    movePage: (id, parent) =>
      run(async () => {
        requirePageResource(resource);
        await updatePageMutation.mutateAsync({
          id,
          values: { parent },
        });
      }),
  };
}

const PAGE_MODEL = "knowledge.Page";

type RowRecord = BaseRecord & Row;

function requirePageResource(
  resource: NonNullable<ReturnType<typeof useModelMetadata>>["resource"] | null,
): asserts resource is NonNullable<ReturnType<typeof useModelMetadata>>["resource"] {
  if (!resource) {
    throw new Error(`Resource metadata for "${PAGE_MODEL}" is not available.`);
  }
}
