import type { TypedDocumentNode } from "@graphql-typed-document-node/core";

export type { TypedDocumentNode };

/** Result data carried by a generated GraphQL `TypedDocumentNode`. */
export type DocumentData<TDocument> =
  TDocument extends TypedDocumentNode<infer TData, any> ? TData : never;

/** Variables carried by a generated GraphQL `TypedDocumentNode`. */
export type DocumentVariables<TDocument> =
  TDocument extends TypedDocumentNode<any, infer TVariables> ? TVariables : never;
