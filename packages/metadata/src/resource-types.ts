// The resource filter/order type contract for refine-backed resource hooks.
//
// `ResourceTypeMap` is an OPEN interface. By default it is empty, so any model
// name is accepted (`ResourceTypeName` widens to `string`) and filter/order
// types widen to `Record<string, unknown>`. A downstream project can register a
// generated model's `Filter`/`Order` inputs by declaration-merging this
// interface to get strict per-model typing.

// eslint-disable-next-line @typescript-eslint/no-empty-object-type -- augmentation target
export interface ResourceTypeMap {}

export type ResourceTypeName = keyof ResourceTypeMap extends never
  ? string
  : keyof ResourceTypeMap;

export type ResourceFilter<TName extends ResourceTypeName> =
  TName extends keyof ResourceTypeMap
    ? ResourceTypeMap[TName] extends { Filter: infer F }
      ? F
      : Record<string, unknown>
    : Record<string, unknown>;

export type ResourceOrder<TName extends ResourceTypeName> =
  TName extends keyof ResourceTypeMap
    ? ResourceTypeMap[TName] extends { Order: infer O }
      ? O
      : Record<string, unknown>
    : Record<string, unknown>;
