// @angee/sdk — the headless contract layer. The SDL is the source of truth; the
// SDK builds documents against it, runs them through one urql client per named
// schema, normalizes the cache, and keeps it live from the change firehose. No
// rendering lives here — that is the rendered binding's job.

// Runtime document assembly.
export { typeNameForModel } from "./selection";

// Transport: clients, cache config, CSRF, per-schema provider.
export {
  cacheConfigFromSchema,
  cacheConfigFromSDL,
  type CacheConfig,
} from "./cache-config";
export {
  createUrqlClient,
  createCsrfTokenProvider,
  graphQLWebSocketUrl,
  isFatalGraphQLWsClose,
  isFatalGraphQLWsCloseCode,
  sessionAuth,
  bearerAuth,
  type AngeeUrqlClientOptions,
  type AuthFetch,
  type CsrfTokenProvider,
  type CsrfTokenOptions,
} from "./graphql-client";
export {
  GraphQLProvider,
  GraphQLClientProvider,
  useActiveGraphQLClientMaybe,
  useGraphQLProviderAvailable,
  useResetClient,
  useSchemaClients,
} from "./graphql-provider";
export {
  EMPTY_SCHEMA_FIELD_METADATA,
  ModelMetadataProvider,
  defineAngeeSchemaMetadata,
  fieldMetadataFromSchema,
  fieldMetadataFromSDL,
  modelMetadataForLabel,
  useModelMetadata,
  useModelRootFields,
  useSchemaFieldMetadata,
  type ModelEnumValueMetadata,
  type ModelFieldKind,
  type ModelFieldMetadata,
  type ModelMetadata,
  type ModelRelationFilterMetadata,
  type ModelRelationFilterMode,
  type ModelRootFieldMetadata,
  type SchemaFieldMetadata,
  type AngeeSchemaMetadata,
  type DataResourceAggregateMeasureMetadata,
  type DataResourceDefaultSortMetadata,
  type DataResourceFieldMetadata,
  type DataResourceGroupAliasMetadata,
  type DataResourceGroupDimensionMetadata,
  type DataResourceGroupExtractionMetadata,
  type DataResourceMetadata,
  type DataResourceRelationAxisMetadata,
  type DataResourceRootMetadata,
  type DataResourceTypeMetadata,
} from "./model-metadata";

// Authored (bespoke) operations.
export type { TypedDocumentNode } from "@urql/core";
export {
  useAuthoredQuery,
  useAuthoredRows,
  useAuthoredMutation,
  useAuthoredSubscription,
  type AuthoredRowsOptions,
  type AuthoredRowsResult,
  type AuthoredStringIdRow,
  type AuthoredQueryOptions,
  type AuthoredQueryResult,
  type AuthoredMutate,
  type AuthoredMutationOptions,
  type AuthoredSubscriptionOptions,
} from "./authored-hooks";
export {
  useDocumentSubscription,
  type DocumentSubscriptionOptions,
  type DocumentSubscriptionRun,
} from "./document-subscription";
export {
  type DocumentData,
  type DocumentVariables,
} from "./typed-document";

// Action-mutation result handling.
export {
  runActionResult,
  type ActionOutcome,
  type ByIdVariables,
} from "./action-result";
// Single-id action mutations derived from a field name (no authored document).
export { useActionMutation, type ActionMutate } from "./action-hooks";

// Live invalidation.
export {
  RelayInvalidationProvider,
  useRegisterModelRefetch,
  useRegisterModelsRefetch,
  useModelInvalidation,
  useInvalidateModels,
  changeSubscriptionDocument,
  changeSubscriptionFields,
} from "./relay-invalidation";

// Cross-cutting context: runtime registry and the context factory.
export { makeContext, type ContextBinding } from "./make-context";
export {
  AppRuntimeProvider,
  useAppRuntime,
  useWidget,
  useFormOverride,
  useModelRoute,
  useMenus,
  useSlot,
  usePreviews,
  useT,
  useNamespaceT,
  type AppRuntime,
} from "./runtime";

// i18n helpers.
export {
  interpolateMessage,
  translateWithFallback,
  type I18nResources,
  type MessageResources,
  type MessageVars,
} from "./i18n";

// Addon composition.
export {
  defineAddon,
  composeAddons,
  mergeChatterContributions,
  mergeSlotContributions,
  type AddonManifest,
  type AddonRoute,
  type ComposedAddons,
  type ComposedMenuItem,
  type ChatterContribution,
  type SlotContribution,
  type PreviewContribution,
  type MenuItem,
  type WidgetMap,
  type FormOverrideMap,
} from "./define-addon";
