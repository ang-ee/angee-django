import { useMemo } from "react";
import type { UseInvalidateProp } from "@refinedev/core";

import {
  canonicalModelLabel,
  canonicalModelLabelOrNull,
} from "./canonical-model-label";
import {
  modelMetadataForLabel,
  type SchemaFieldMetadata,
} from "./artifact";
import { useSchemaFieldMetadata } from "./context";
import { refineResourceIdentifier } from "./resources";

export interface ResourceInvalidationTarget {
  resource: string;
  dataProviderName: string;
  /** Exact written record when the operation declares its identity. */
  id?: string;
}

export function resourceInvalidationTargets(
  metadata: SchemaFieldMetadata,
  modelLabels: readonly string[],
  records?: readonly { model: string; id?: string }[],
): readonly ResourceInvalidationTarget[] {
  if (modelLabels.length === 0) return [];
  return modelLabels.flatMap((spelling) => {
    const modelLabel = canonicalModelLabel(metadata.resources ?? [], spelling);
    const model = modelMetadataForLabel(metadata, modelLabel);
    const resource = model?.resource;
    if (!resource) {
      throw new Error(
        `Action invalidation target "${spelling}" is not exposed in resource metadata.`,
      );
    }
    const target = {
      resource: refineResourceIdentifier(resource),
      dataProviderName: resource.schemaName,
    };
    const matching = records?.filter((record) =>
      canonicalModelLabel(metadata.resources ?? [], record.model, { optional: true }) === modelLabel);
    // A model-only effect deliberately retains the broad fallback. Collections
    // still reconcile for exact writes because membership/order/totals are owned
    // by their server query, not by a single returned row.
    if (!matching?.length || matching.some((record) => !record.id)) return [target];
    return [...new Set(matching.flatMap((record) => record.id ? [record.id] : []))].map((id) => ({ ...target, id }));
  });
}

export function refineInvalidationParams(
  target: ResourceInvalidationTarget,
): UseInvalidateProp {
  return {
    resource: target.resource,
    dataProviderName: target.dataProviderName,
    ...(target.id !== undefined ? {
      id: target.id,
      invalidates: ["list", "many", "detail"],
    } : {
      // Unknown identity requires the whole resource prefix, including every
      // detail projection; Refine's detail invalidation always requires an id.
      invalidates: ["resourceAll"],
    }),
  };
}

type InvalidationRecord = { model: string; id?: string };
type InvalidationRecords<TData, TVariables> = readonly InvalidationRecord[]
  | ((data: TData, variables: TVariables) => readonly InvalidationRecord[]);
type InvalidationParams<TData, TVariables> = readonly UseInvalidateProp[]
  | ((data: TData, variables: TVariables) => readonly UseInvalidateProp[]);

/**
 * The refine `invalidates` a verb's mutated Angee model labels map to.
 *
 * The one fold of model labels through this module's pair
 * ({@link resourceInvalidationTargets} → {@link refineInvalidationParams}): a
 * hook that moves a named model's resource caches composes this instead of
 * repeating the pair against the ambient metadata. Stabilized by label contents,
 * so a caller passing a fresh array literal each render does not churn the
 * mutation options it feeds. Declared record effects may be static or resolved
 * from the mutation response/variables; unknown model identities stay broad.
 */
export function useResourceInvalidates(modelLabels: readonly string[] | undefined): readonly UseInvalidateProp[];
export function useResourceInvalidates<TData, TVariables>(
  modelLabels: readonly string[] | undefined,
  records: InvalidationRecords<TData, TVariables> | undefined,
): InvalidationParams<TData, TVariables>;
export function useResourceInvalidates<TData, TVariables>(
  modelLabels: readonly string[] | undefined,
  records?: InvalidationRecords<TData, TVariables>,
): InvalidationParams<TData, TVariables> {
  const metadata = useSchemaFieldMetadata();
  const canonicalModelLabels = useCanonicalResourceModelLabels(modelLabels);
  const key = JSON.stringify(canonicalModelLabels);
  return useMemo(() => {
    const resolve = (effects: readonly InvalidationRecord[] | undefined) => {
      // Authored subscriptions may observe models with no native CRUD resource.
      // Only exposed resources enter this map; the mutation retains all effects
      // for Refine's separate authored/live reconciliation path.
      const resourceModels = (effects ?? []).flatMap((record) => {
        const label = canonicalModelLabel(metadata.resources ?? [], record.model, { optional: true });
        return label ? [label] : [];
      });
      const models = [...new Set([...canonicalModelLabels, ...resourceModels])];
      return resourceInvalidationTargets(metadata, models, effects).map(refineInvalidationParams);
    };
    return typeof records === "function"
      ? (data: TData, variables: TVariables) => resolve(records(data, variables))
      : resolve(records);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, metadata, records]);
}

/**
 * Preserve authored model effects while selecting their native-resource subset.
 * Resource aliases resolve through the emitted inventory. Other effects retain
 * their declared canonical labels; resource metadata cannot validate authored
 * query/subscription models. Ambiguous resource aliases still fail fast.
 */
export function useResourceMutationModels(modelLabels: readonly string[] | undefined): {
  models: readonly string[];
  resourceModels: readonly string[];
} {
  const metadata = useSchemaFieldMetadata();
  const key = JSON.stringify(modelLabels ?? []);
  return useMemo(() => {
    const resources = new Set<string>();
    const models = (modelLabels ?? []).map((spelling) => {
      const resource = canonicalModelLabel(metadata.resources, spelling, { optional: true });
      if (resource) resources.add(resource);
      return resource ?? spelling;
    });
    return { models: [...new Set(models)], resourceModels: [...resources] };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, metadata]);
}

/**
 * Resolve authored-operation model labels at the rendered metadata edge.
 * Refine's authored hooks intentionally stay metadata-free and require exact
 * canonical labels; render-time unknowns warn in development and are omitted.
 */
export function useCanonicalResourceModelLabels(
  modelLabels: readonly string[] | undefined,
): readonly string[] {
  const metadata = useSchemaFieldMetadata();
  const key = JSON.stringify(modelLabels ?? []);
  return useMemo(() => {
    const canonical = (modelLabels ?? []).flatMap((spelling) => {
      const label = canonicalModelLabelOrNull(
        metadata.resources ?? [],
        spelling,
        "authored-operation model label",
      );
      return label ? [label] : [];
    });
    return [...new Set(canonical)];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, metadata]);
}
