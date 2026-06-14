// @angee/sdk — the headless contract layer. The SDL is the source of truth; the
// SDK builds documents against it, runs them through one urql client per named
// schema, normalizes the cache, and keeps it live from the change firehose. No
// rendering lives here — that is the rendered binding's job.

// Runtime document assembly.
export {
  assembleDetailDocument,
  assembleListDocument,
  assembleMutationDocument,
  assembleRevisionsDocument,
  assembleAggregateDocument,
  assembleGroupByDocument,
  typeNameForModel,
  toRelayGlobalId,
  relationRelayGlobalId,
  clampPageSize,
  DEFAULT_PAGE_SIZE,
  MAX_PAGE_SIZE,
  type SelectionField,
  type MutationAction,
  type AssembleListDocumentOptions,
  type AssembleAggregateDocumentOptions,
  type AssembleGroupByDocumentOptions,
} from "./selection";

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
  useResetClient,
  useSchemaClients,
} from "./graphql-provider";
export {
  EMPTY_SCHEMA_FIELD_METADATA,
  ModelMetadataProvider,
  fieldMetadataFromSchema,
  fieldMetadataFromSDL,
  modelMetadataForLabel,
  useModelMetadata,
  useSchemaFieldMetadata,
  type ModelEnumValueMetadata,
  type ModelFieldKind,
  type ModelFieldMetadata,
  type ModelMetadata,
  type ModelRootFieldMetadata,
  type SchemaFieldMetadata,
} from "./model-metadata";

// Resource data access.
export {
  useResourceList,
  useResourceRecord,
  useResourceRevisions,
  useResourceMutation,
  type UseResourceListOptions,
  type UseResourceListResult,
  type UseResourceRecordResult,
  type UseResourceRevisionsOptions,
  type UseResourceRevisionsResult,
  type ResourceMutate,
  type ResourceMutationVariables,
  type ResourceMutationResult,
} from "./resource-hooks";
export {
  validationErrorsFromError,
  type ValidationErrors,
} from "./validation-errors";
export type {
  Row,
  ResourceRevision,
  PageInfo,
  PageResult,
  DeletePreview,
  DeletePreviewGroup,
  DeletePreviewNode,
} from "./resource-result";

// Aggregates.
export {
  useResourceAggregate,
  useResourceGroupBy,
  type GroupByDimension,
  type GroupByOrder,
  type UseAggregateOptions,
  type UseGroupByOptions,
} from "./aggregates";
export {
  autoExtractAggregate,
  autoExtractGroupBy,
  type AggregateBucket,
  type AggregateMeasure,
  type AggregateMeasureOperator,
  type GroupByResult,
} from "./aggregate-extract";

// Authored (bespoke) operations.
export {
  useAuthoredQuery,
  useAuthoredMutation,
  useAuthoredSubscription,
  type AuthoredQueryOptions,
  type AuthoredQueryResult,
  type AuthoredMutate,
  type AuthoredSubscriptionOptions,
} from "./authored-hooks";

// Action-mutation result handling.
export { runActionResult } from "./action-result";

// Live invalidation.
export {
  RelayInvalidationProvider,
  useRegisterModelRefetch,
  useModelInvalidation,
  useInvalidateModels,
  changeSubscriptionDocument,
  changeSubscriptionFields,
} from "./relay-invalidation";

// Cross-cutting context: runtime registry, auth, the context factory.
export { makeContext, type ContextBinding } from "./make-context";
export {
  AppRuntimeProvider,
  useAppRuntime,
  useWidget,
  useFormOverride,
  useMenus,
  useSlot,
  useT,
  type AppRuntime,
} from "./runtime";
export {
  AuthProvider,
  useAuth,
  currentUserToAuthState,
  ANONYMOUS_AUTH,
  type AuthState,
  type AuthUser,
  type CurrentUserPayload,
} from "./auth";
export {
  useRuntimeAuthState,
  useLoginWithPassword,
  useLogout,
  CURRENT_USER_DOCUMENT,
  LOGIN_DOCUMENT,
  LOGOUT_DOCUMENT,
  type LoginCredentials,
  type LoginResult,
} from "./auth-hooks";

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
  type MenuItem,
  type WidgetMap,
  type FormOverrideMap,
} from "./define-addon";

// Generated, per-schema type contracts.
export type {
  ResourceTypeMap,
  ResourceTypeName,
  ResourceFilter,
  ResourceOrder,
} from "./__generated__/resource-types";
