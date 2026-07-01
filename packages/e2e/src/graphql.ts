import type { APIRequestContext } from "@playwright/test";

/** GraphQL endpoints and the CSRF endpoint, reached through the SPA origin. */
export const PUBLIC_GRAPHQL_PATH = "/graphql/public/";
export const CSRF_PATH = "/auth/csrf/";

export interface GraphQLError {
  message: string;
  extensions?: { code?: string };
}

export interface GraphQLResult<T = unknown> {
  data?: T;
  errors?: GraphQLError[];
}

/**
 * A GraphQL caller bound to a Playwright request context. It carries the session
 * cookie already in the context and adds the Django CSRF header, mirroring the
 * SPA's own transport (`@angee/sdk` `graphql-client`) — so a test speaks to the
 * backend exactly as the running app does. Requests go through the SPA origin
 * (the Vite proxy), keeping cookies same-origin with the browser.
 */
export class GraphQLClient {
  readonly #request: APIRequestContext;
  readonly #path: string;
  #csrf: string | undefined;

  constructor(request: APIRequestContext, path: string = PUBLIC_GRAPHQL_PATH) {
    this.#request = request;
    this.#path = path;
  }

  /** Fetch the CSRF token once (the GET also sets the csrftoken cookie). */
  async #token(): Promise<string> {
    if (this.#csrf !== undefined) return this.#csrf;
    const response = await this.#request.get(CSRF_PATH);
    if (!response.ok()) return (this.#csrf = "");
    const body = (await response.json()) as { token?: unknown };
    return (this.#csrf = typeof body.token === "string" ? body.token : "");
  }

  async query<T = unknown>(
    query: string,
    variables: Record<string, unknown> = {},
  ): Promise<GraphQLResult<T>> {
    const token = await this.#token();
    const response = await this.#request.post(this.#path, {
      headers: token ? { "x-csrftoken": token } : {},
      data: { query, variables },
    });
    return (await response.json()) as GraphQLResult<T>;
  }
}
