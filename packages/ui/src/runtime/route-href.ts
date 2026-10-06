/** Query keys owned by shared record routing; addon detail state declares its own keys. */
export const RECORD_NAVIGATION_SEARCH_KEY = "recordNav";
export const RECORD_TAB_SEARCH_KEY = "recordTab";
export const RECORD_SEARCH_KEYS: readonly string[] = [RECORD_NAVIGATION_SEARCH_KEY, RECORD_TAB_SEARCH_KEY];

/** The route facts a rendered consumer may ask the composed app for. */
export interface RuntimeRouteDescriptor {
  name: string;
  path: string;
}

export type RouteHrefParams = Readonly<Record<string, string | number>>;

export type RouteHrefSearchValue = string | number | undefined;

export type RouteHrefSearch =
  | string
  | Readonly<Record<string, RouteHrefSearchValue>>;

/**
 * A record route's claim: the record's `field` (a dotted path) equals `equals`,
 * or one of its values when it is a list.
 */
export interface RecordMatch {
  field: string;
  equals: string | readonly string[];
}

/** The values a record match accepts. */
export function recordMatchValues(match: RecordMatch): readonly string[] {
  return typeof match.equals === "string" ? [match.equals] : match.equals;
}

/** Collection/record route names selected by the app's resource projection. */
export interface RuntimeResourceRoutes {
  collection: string;
  record?: { name: string; param: string };
  recordDestinations?: readonly { record: { name: string; param: string }; match: RecordMatch }[];
  recordFallback?: { name: string; param: string };
}

/** Build one href from a composed route name, its params, and optional search. */
export interface RouteHref {
  (
    name: string,
    params?: RouteHrefParams,
    search?: RouteHrefSearch,
  ): string;
  /** Probe an optional/cross-addon route without turning absence into a render error. */
  maybe(
    name: string,
    params?: RouteHrefParams,
    search?: RouteHrefSearch,
  ): string | undefined;
}

/** A route spelling absent from the composed app. */
export class UnknownRouteError extends Error {
  readonly routeName: string;

  constructor(routeName: string) {
    super(`Unknown route name "${routeName}".`);
    this.name = "UnknownRouteError";
    this.routeName = routeName;
  }
}

/**
 * Compile the app's composed route descriptors into its runtime href owner.
 * Route names and parameter sets are exact: unknown routes, missing params, and
 * extra params are composition/authoring defects and fail at the call site.
 */
export function createRouteHref(
  descriptors: readonly RuntimeRouteDescriptor[],
  /** Routes composition made unavailable: `maybe` returns nothing for them; strict calls still build. */
  options: { unavailable?: ReadonlySet<string> } = {},
): RouteHref {
  for (const descriptor of descriptors) validateRouteTemplate(descriptor);
  const routesByName = new Map(
    descriptors.map((descriptor) => [descriptor.name, descriptor.path]),
  );

  const resolve = (name: string, params: RouteHrefParams = {}, search?: RouteHrefSearch) => {
    const template = routesByName.get(name);
    if (template === undefined) {
      throw new UnknownRouteError(name);
    }

    const parameterNames = [...new Set(routeParameterNames(template))];
    const missing = parameterNames.filter(
      (parameter) =>
        !Object.prototype.hasOwnProperty.call(params, parameter)
        || params[parameter] == null,
    );
    if (missing.length > 0) {
      throw new Error(
        `Route "${name}" is missing params: ${missing.join(", ")}.`,
      );
    }

    const expected = new Set(parameterNames);
    const extra = Object.keys(params).filter((parameter) => !expected.has(parameter));
    if (extra.length > 0) {
      throw new Error(
        `Route "${name}" received extra params: ${extra.sort().join(", ")}.`,
      );
    }

    const href = template
      .split("/")
      .map((segment) => {
        const parameter = routeParameterName(segment);
        return parameter === undefined
          ? segment
          : encodeURIComponent(String(params[parameter]));
      })
      .join("/");
    const query = routeSearchString(search);
    return query ? `${href}?${query}` : href;
  };

  return Object.assign(resolve, {
    maybe(name: string, params?: RouteHrefParams, search?: RouteHrefSearch) {
      if (options.unavailable?.has(name)) return undefined;
      try {
        return resolve(name, params, search);
      } catch (error) {
        if (error instanceof UnknownRouteError) return undefined;
        throw error;
      }
    },
  });
}

/**
 * A route href that builds some route names as others. Inside a mount, the
 * mounted route's names build the alias family's (`iam.users` → `x.people`,
 * `iam.users.record` → `x.people.record`), so a borrowed page's own links stay
 * in the borrowing app; every other name builds unchanged.
 */
export function aliasRouteHref(
  routeHref: RouteHref,
  aliases: ReadonlyMap<string, string>,
): RouteHref {
  const aliased = (name: string): string => aliases.get(name) ?? name;
  return Object.assign(
    (name: string, params?: RouteHrefParams, search?: RouteHrefSearch) => routeHref(aliased(name), params, search),
    {
      maybe: (name: string, params?: RouteHrefParams, search?: RouteHrefSearch) =>
        routeHref.maybe(aliased(name), params, search),
    },
  );
}

function routeParameterNames(template: string): string[] {
  return template
    .split("/")
    .flatMap((segment) => {
      const parameter = routeParameterName(segment);
      return parameter === undefined ? [] : [parameter];
    });
}

/** Parse one complete TanStack `$param` segment. */
export function routeParameterName(segment: string): string | undefined {
  return /^\$([A-Za-z_][A-Za-z0-9_]*)$/.exec(segment)?.[1];
}

function validateRouteTemplate(descriptor: RuntimeRouteDescriptor): void {
  for (const segment of descriptor.path.split("/")) {
    if (routeParameterName(segment) !== undefined) continue;
    if (!segment.includes("$") && !segment.includes("{")) continue;
    throw new Error(
      `Route "${descriptor.name}" has unsupported template segment "${segment}".`,
    );
  }
}

/** Serialize flat search values for both runtime hrefs and the app router. */
export function routeSearchString(
  search: string | Readonly<Record<string, unknown>> | undefined,
): string {
  if (search === undefined) return "";
  if (typeof search === "string") return search.replace(/^\?/, "");

  const params = new URLSearchParams();
  for (const [name, value] of Object.entries(search)) {
    // An explicit empty value clears seeded search defaults; absence restores them.
    if (value != null) params.set(name, String(value));
  }
  return params.toString();
}
