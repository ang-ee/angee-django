import {
  createContext,
  createElement,
  useContext,
  type ReactNode,
} from "react";
export type ActionDocumentMap = Readonly<Record<string, unknown>>;
export type AggregateDocumentMap = Readonly<Record<string, unknown>>;
export type DeletePreviewDocumentMap = Readonly<Record<string, unknown>>;
export type GroupDocumentMap = Readonly<Record<string, unknown>>;
export type RevisionDocumentMap = Readonly<Record<string, unknown>>;
export type SaveDocumentMap = Readonly<Record<string, unknown>>;

export interface SchemaOperationDocuments {
  actions?: ActionDocumentMap;
  aggregates?: AggregateDocumentMap;
  deletePreviews?: DeletePreviewDocumentMap;
  groups?: GroupDocumentMap;
  revisions?: RevisionDocumentMap;
  /** Authored `<resource>_save(pk, patch, lines)` diff-apply documents (F6). */
  saves?: SaveDocumentMap;
}

export type OperationDocumentsBySchema =
  Readonly<Record<string, SchemaOperationDocuments | undefined>>;
export type OperationDocumentKind = keyof SchemaOperationDocuments;

const OPERATION_DOCUMENT_LABELS = {
  actions: "action",
  aggregates: "aggregate",
  deletePreviews: "delete-preview",
  groups: "group",
  revisions: "revision",
  saves: "save",
} as const satisfies Record<OperationDocumentKind, string>;

const OperationDocumentsContext =
  createContext<OperationDocumentsBySchema>({});

export function OperationDocumentsProvider({
  documents,
  children,
}: {
  documents?: OperationDocumentsBySchema;
  children: ReactNode;
}): ReactNode {
  return createElement(OperationDocumentsContext.Provider, {
    value: documents ?? {},
    children,
  });
}

export function useOperationDocuments(): OperationDocumentsBySchema {
  return useContext(OperationDocumentsContext);
}

export function operationDocument(
  documents: OperationDocumentsBySchema,
  schema: string,
  kind: OperationDocumentKind,
  key: string,
): unknown {
  const document = maybeOperationDocument(documents, schema, kind, key);
  if (!document) {
    const label = OPERATION_DOCUMENT_LABELS[kind];
    throw new Error(
      `No generated ${label} document for "${key}" in schema "${schema}". ` +
        "Run project codegen and pass schema.operationDocuments to createApp.",
    );
  }
  return document;
}

/**
 * The non-throwing lookup for capability probes that run on every render
 * (aggregate/groups/deletePreview availability): a missing document reads as
 * "capability absent" instead of failing the render; `operationDocument`
 * stays fail-fast for callers about to execute an operation.
 */
export function maybeOperationDocument(
  documents: OperationDocumentsBySchema,
  schema: string,
  kind: OperationDocumentKind,
  key: string,
): unknown {
  return documents[schema]?.[kind]?.[key] ?? null;
}

export function actionDocumentForSchema(
  documents: OperationDocumentsBySchema,
  schema: string,
  field: string,
): unknown {
  return operationDocument(documents, schema, "actions", field);
}

export function aggregateDocumentForResource(
  documents: OperationDocumentsBySchema,
  schema: string,
  resource: string,
): unknown {
  return operationDocument(documents, schema, "aggregates", resource);
}

export function deletePreviewDocumentForResource(
  documents: OperationDocumentsBySchema,
  schema: string,
  resource: string,
): unknown {
  return operationDocument(documents, schema, "deletePreviews", resource);
}

export function groupDocumentForResource(
  documents: OperationDocumentsBySchema,
  schema: string,
  resource: string,
): unknown {
  return operationDocument(documents, schema, "groups", resource);
}

export function revisionDocumentForResource(
  documents: OperationDocumentsBySchema,
  schema: string,
  resource: string,
): unknown {
  return operationDocument(documents, schema, "revisions", resource);
}
