# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: auth.setup.ts >> authenticate bob
- Location: tests/auth.setup.ts:15:3

# Error details

```
SyntaxError: Unexpected token 'A', "Account lo"... is not valid JSON
```

# Test source

```ts
  1  | import type { APIRequestContext } from "@playwright/test";
  2  | 
  3  | /** GraphQL endpoints and the CSRF endpoint, reached through the SPA origin. */
  4  | export const PUBLIC_GRAPHQL_PATH = "/graphql/public/";
  5  | export const CSRF_PATH = "/auth/csrf/";
  6  | 
  7  | export interface GraphQLError {
  8  |   message: string;
  9  |   extensions?: { code?: string };
  10 | }
  11 | 
  12 | export interface GraphQLResult<T = unknown> {
  13 |   data?: T;
  14 |   errors?: GraphQLError[];
  15 | }
  16 | 
  17 | /**
  18 |  * A GraphQL caller bound to a Playwright request context. It carries the session
  19 |  * cookie already in the context and adds the Django CSRF header, mirroring the
  20 |  * SPA's own transport — so a test speaks to the
  21 |  * backend exactly as the running app does. Requests go through the SPA origin
  22 |  * (the Vite proxy), keeping cookies same-origin with the browser.
  23 |  */
  24 | export class GraphQLClient {
  25 |   readonly #request: APIRequestContext;
  26 |   readonly #path: string;
  27 |   #csrf: string | undefined;
  28 | 
  29 |   constructor(request: APIRequestContext, path: string = PUBLIC_GRAPHQL_PATH) {
  30 |     this.#request = request;
  31 |     this.#path = path;
  32 |   }
  33 | 
  34 |   /** Fetch the CSRF token once (the GET also sets the csrftoken cookie). */
  35 |   async #token(): Promise<string> {
  36 |     if (this.#csrf !== undefined) return this.#csrf;
  37 |     const response = await this.#request.get(CSRF_PATH);
  38 |     if (!response.ok()) return (this.#csrf = "");
  39 |     const body = (await response.json()) as { token?: unknown };
  40 |     return (this.#csrf = typeof body.token === "string" ? body.token : "");
  41 |   }
  42 | 
  43 |   async query<T = unknown>(
  44 |     query: string,
  45 |     variables: Record<string, unknown> = {},
  46 |   ): Promise<GraphQLResult<T>> {
  47 |     const token = await this.#token();
  48 |     const response = await this.#request.post(this.#path, {
  49 |       headers: token ? { "x-csrftoken": token } : {},
  50 |       data: { query, variables },
  51 |     });
> 52 |     return (await response.json()) as GraphQLResult<T>;
     |             ^ SyntaxError: Unexpected token 'A', "Account lo"... is not valid JSON
  53 |   }
  54 | }
  55 | 
```