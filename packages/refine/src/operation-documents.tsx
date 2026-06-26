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

export interface SchemaOperationDocuments {
  actions?: ActionDocumentMap;
  aggregates?: AggregateDocumentMap;
  deletePreviews?: DeletePreviewDocumentMap;
  groups?: GroupDocumentMap;
  revisions?: RevisionDocumentMap;
}

export type OperationDocumentsBySchema =
  Readonly<Record<string, SchemaOperationDocuments | undefined>>;

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

export function actionDocumentForSchema(
  documents: OperationDocumentsBySchema,
  schema: string,
  field: string,
): unknown {
  const document = documents[schema]?.actions?.[field];
  if (!document) {
    throw new Error(
      `No generated action document for "${field}" in schema "${schema}". ` +
        "Run project codegen and pass schema.operationDocuments to createApp.",
    );
  }
  return document;
}

export function aggregateDocumentForResource(
  documents: OperationDocumentsBySchema,
  schema: string,
  resource: string,
): unknown {
  const document = documents[schema]?.aggregates?.[resource];
  if (!document) {
    throw new Error(
      `No generated aggregate document for resource "${resource}" ` +
        `in schema "${schema}". Run project codegen and pass ` +
        "schema.operationDocuments to createApp.",
    );
  }
  return document;
}

export function deletePreviewDocumentForResource(
  documents: OperationDocumentsBySchema,
  schema: string,
  resource: string,
): unknown {
  const document = documents[schema]?.deletePreviews?.[resource];
  if (!document) {
    throw new Error(
      `No generated delete-preview document for resource "${resource}" ` +
        `in schema "${schema}". Run project codegen and pass ` +
        "schema.operationDocuments to createApp.",
    );
  }
  return document;
}

export function groupDocumentForResource(
  documents: OperationDocumentsBySchema,
  schema: string,
  resource: string,
): unknown {
  const document = documents[schema]?.groups?.[resource];
  if (!document) {
    throw new Error(
      `No generated group document for resource "${resource}" ` +
        `in schema "${schema}". Run project codegen and pass ` +
        "schema.operationDocuments to createApp.",
    );
  }
  return document;
}

export function revisionDocumentForResource(
  documents: OperationDocumentsBySchema,
  schema: string,
  resource: string,
): unknown {
  const document = documents[schema]?.revisions?.[resource];
  if (!document) {
    throw new Error(
      `No generated revision document for resource "${resource}" ` +
        `in schema "${schema}". Run project codegen and pass ` +
        "schema.operationDocuments to createApp.",
    );
  }
  return document;
}
