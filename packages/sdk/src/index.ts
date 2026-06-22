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
  assembleFacetsDocument,
  typeNameForModel,
  publicIdLabel,
  clampPageSize,
  DEFAULT_PAGE_SIZE,
  MAX_PAGE_SIZE,
  type SelectionField,
  type MutationAction,
  type AssembleListDocumentOptions,
  type AssembleAggregateDocumentOptions,
  type AssembleFacetDocumentSpec,
  type AssembleFacetsDocumentOptions,
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
  type DataQueryRelationAxisMetadata,
  type DataQueryRootMetadata,
  type DataQuerySurfaceMetadata,
  type DataQueryTypeMetadata,
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
export {
  createGraphQLDataSource,
  DATA_VIEW_GROUP_GRANULARITIES,
  DATA_VIEW_KINDS,
  DATA_VIEW_LOOKUP_OPERATORS,
  DATA_VIEW_RELATION_LOOKUP_OPERATORS,
  DATA_VIEW_SEARCH_KEYS,
  createLocalRowsDataSource,
  dataQueryGroupField,
  dataQueryGroupKey,
  dataQueryPage,
  DataViewState,
  DEFAULT_DATA_VIEW_PAGE_SIZE,
  DEFAULT_TEXT_FILTER_FIELD,
  Filter,
  dataViewFavoritesFromJson,
  dataViewGroupsEqual,
  dataViewSearchToState,
  dataViewStateToSearch,
  extractResourceFacetResults,
  isLookupOperator,
  localRowsFilter,
  localRowsSort,
  mergeDataViewSearch,
  nextRowTextFilter,
  readPath,
  rowTextFilterValue,
  stableSerialize,
  useResourceFacets,
  useGraphQLDataSource,
  type DataViewAction,
  type DataViewDefaultGroups,
  type DataViewFacetLookupOperator,
  type DataViewFavorite,
  type DataViewFilter,
  type DataViewFilterPrimitive,
  type DataViewFilterValue,
  type DataViewGroup,
  type DataViewGroupGranularity,
  type DataViewInitialState,
  type DataViewKind,
  type DataViewLookup,
  type DataViewLookupOperator,
  type DataViewOrderDirection,
  type DataViewRelationLookupOperator,
  type DataViewResourceOrder,
  type DataViewSearch,
  type DataViewSearchKey,
  type DataViewSort,
  type DataViewSortDirection,
  type DataQuery,
  type DataQueryFilter,
  type DataQueryGroup,
  type DataQueryGroupOrder,
  type DataQueryOrder,
  type FilterFacet,
  type GraphQLFacetQuery,
  type GraphQLFacetsQuery,
  type GraphQLAggregateQuery,
  type GraphQLDataSource,
  type GraphQLGroupByQuery,
  type GraphQLListDocumentQuery,
  type GraphQLListVariablesQuery,
  type LocalRowsDataSource,
  type LocalRowsQuery,
  type LocalRowsResult,
  type ResourceFacetOption,
  type ResourceFacetResult,
  type ResourceFacetSpec,
  type UseResourceFacetsOptions,
} from "./data";
export type {
  Row,
  ResourceRevision,
  PageInfo,
  PageResult,
  DeletePreview,
  DeletePreviewGroup,
  DeletePreviewNode,
} from "./resource-result";
export { revisionSnapshot, rowPublicId } from "./resource-result";

// Aggregates.
export {
  bucketKey,
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
export { errorMessage } from "./error-message";
export { useBusyRun, type BusyRun } from "./use-busy-run";

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
  useModelRoute,
  useMenus,
  useSlot,
  usePreviews,
  useT,
  useNamespaceT,
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
  type UserPreferences,
} from "./auth";
export {
  useRuntimeAuthState,
  useLoginWithPassword,
  useLogout,
  useUpdatePreferences,
  CURRENT_USER_DOCUMENT,
  LOGIN_DOCUMENT,
  LOGOUT_DOCUMENT,
  UPDATE_PREFERENCES_DOCUMENT,
  type LoginCredentials,
  type LoginResult,
} from "./auth-hooks";
export {
  UserPreferencesProvider,
  useUserPreferences,
  type UserPreferencesState,
} from "./preferences";

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

// The resource filter/order type contract (open for downstream augmentation).
export type {
  ResourceTypeMap,
  ResourceTypeName,
  ResourceFilter,
  ResourceOrder,
} from "./resource-types";
