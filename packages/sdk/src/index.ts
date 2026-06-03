// @angee/sdk — the headless contract layer. The SDL is the source of truth; the
// SDK builds documents against it, runs them through one urql client per named
// schema, normalizes the cache, and keeps it live from the change firehose. No
// rendering lives here — that is the rendered binding's job.

// Runtime document assembly.
export {
  buildSelection,
  printSelection,
  assembleDetailDocument,
  assembleListDocument,
  assembleMutationDocument,
  assembleAggregateDocument,
  assembleGroupByDocument,
  aggregateFieldName,
  groupByFieldName,
  typeNameForModel,
  singularFieldName,
  pluralFieldName,
  clampPageSize,
  DEFAULT_PAGE_SIZE,
  MAX_PAGE_SIZE,
  PAGE_SIZE_OPTIONS,
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
  type AngeeUrqlClientOptions,
  type CsrfTokenProvider,
  type CsrfTokenOptions,
} from "./graphql-client";
export {
  createSchemaClients,
  GraphQLProvider,
  GraphQLClientProvider,
  useResetClient,
  useSchemaClients,
} from "./graphql-provider";

// Resource data access.
export {
  useResourceList,
  useResourceRecord,
  useResourceMutation,
  type UseResourceListOptions,
  type UseResourceListResult,
  type UseResourceRecordResult,
  type ResourceMutate,
  type ResourceMutationVariables,
  type ResourceMutationResult,
} from "./resource-hooks";
export type {
  Row,
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
  type AggregateBucket,
  type AggregateMeasure,
  type AggregateMeasureOperator,
  type GroupByResult,
  type GroupByDimension,
  type GroupByOrder,
  type UseAggregateOptions,
  type UseGroupByOptions,
} from "./aggregates";
export { autoExtractAggregate, autoExtractGroupBy } from "./aggregate-extract";

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

// Live invalidation.
export {
  RelayInvalidationProvider,
  useRegisterModelRefetch,
  useModelInvalidation,
  useInvalidateModels,
  changeSubscriptionDocument,
} from "./relay-invalidation";

// Cross-cutting context: runtime registry, auth, the context factory.
export { makeContext, type ContextBinding } from "./make-context";
export {
  AppRuntimeProvider,
  useAppRuntime,
  useWidget,
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
  type ChatterContribution,
  type SlotContribution,
  type MenuItem,
  type WidgetMap,
} from "./define-addon";

// Generated, per-schema type contracts.
export type {
  ResourceTypeMap,
  ResourceTypeName,
  ResourceFilter,
  ResourceOrder,
} from "./__generated__/resource-types";
