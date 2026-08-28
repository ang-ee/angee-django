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
      try {
        return resolve(name, params, search);
      } catch (error) {
        if (error instanceof UnknownRouteError) return undefined;
        throw error;
      }
    },
  });
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
    if (value != null && value !== "") params.set(name, String(value));
  }
  return params.toString();
}
