import {
  createContext,
  createElement,
  useContext,
  useMemo,
  type ReactNode,
} from "react";

import {
  modelMetadataForLabel,
  type DataResourceRootMetadata,
  type ModelMetadata,
  type SchemaFieldMetadata,
} from "./artifact";

export const EMPTY_SCHEMA_FIELD_METADATA: SchemaFieldMetadata = {
  types: {},
  labels: {},
  resources: [],
};

const ModelMetadataContext = createContext<SchemaFieldMetadata | null>(null);
const ActiveGraphQLSchemaContext = createContext<string | null>(null);

export function ModelMetadataProvider({
  metadata = EMPTY_SCHEMA_FIELD_METADATA,
  children,
}: {
  metadata?: SchemaFieldMetadata;
  children: ReactNode;
}): ReactNode {
  return createElement(ModelMetadataContext.Provider, {
    value: metadata,
    children,
  });
}

export function useModelMetadata(modelLabel: string): ModelMetadata | null {
  const metadata = useSchemaFieldMetadata();
  return useMemo(
    () => (modelLabel ? modelMetadataForLabel(metadata, modelLabel) : null),
    [metadata, modelLabel],
  );
}

export function useSchemaFieldMetadata(): SchemaFieldMetadata {
  return useContext(ModelMetadataContext) ?? EMPTY_SCHEMA_FIELD_METADATA;
}

export function useModelRootFields(modelLabel: string): DataResourceRootMetadata | null;
export function useModelRootFields(
  modelLabel: string,
  options: { required: false },
): DataResourceRootMetadata | null | undefined;
export function useModelRootFields(
  modelLabel: string,
  options: { required: boolean },
): DataResourceRootMetadata | null | undefined;
export function useModelRootFields(
  modelLabel: string,
  options: { required?: boolean } = {},
): DataResourceRootMetadata | null | undefined {
  const metadata = useSchemaFieldMetadata();
  return useMemo(() => {
    if (!modelLabel) return null;
    if (metadata.resources.length === 0) return null;
    const model = modelMetadataForLabel(metadata, modelLabel);
    if (!model) {
      if (options.required === false) return undefined;
      throw new Error(
        `GraphQL schema is configured with SDL but exposes no resource metadata ` +
          `for model "${modelLabel}"; emit it in angee.resources or correct the ` +
          "model label.",
      );
    }
    return model.resource.roots;
  }, [metadata, modelLabel, options.required]);
}

export function ActiveGraphQLSchemaProvider({
  schema,
  children,
}: {
  schema: string;
  children: ReactNode;
}): ReactNode {
  return createElement(ActiveGraphQLSchemaContext.Provider, {
    value: schema,
    children,
  });
}

export function useActiveGraphQLSchemaName(): string | null {
  return useContext(ActiveGraphQLSchemaContext);
}
