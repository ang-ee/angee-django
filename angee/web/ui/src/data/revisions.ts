import {
  useModelMetadata,
} from "@angee/metadata";
import {
  useAngeeRevisions,
  type UseAngeeRevisionsResult,
} from "@angee/refine";
import type { ResourceTypeName } from "@angee/metadata";
import { useRevisionOperation } from "../views/resource-operations";

export interface UseResourceRevisionsOptions {
  enabled?: boolean;
}

// The metadata-aware wrapper adds nothing to the dialect result shape; the
// dialect hook owns it.
export type UseResourceRevisionsResult = UseAngeeRevisionsResult;

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
  const active =
    enabled &&
    id !== null &&
    id !== undefined &&
    id !== "" &&
    resource !== null &&
    Boolean(resource.roots.revisions);
  const operation = useRevisionOperation(resource);
  return useAngeeRevisions(operation.target, id, {
    document: operation.document,
    enabled: active,
  });
}
