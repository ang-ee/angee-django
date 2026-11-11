import {
  GraphQLError,
  Kind,
  parse,
  valueFromASTUntyped,
  type DocumentNode,
  type FieldNode,
  type OperationDefinitionNode,
} from "graphql";

/** A handler throws graphql's own error to answer its request with that error. */
export { GraphQLError };

/** One GraphQL request as the wire fake parsed it. */
export interface GraphQLWireRequest {
  /** The operation's own name (`ResourceList`, an authored document's name), when it has one. */
  operationName?: string;
  /** The root fields the operation selects, by schema name rather than alias, in document order. */
  fields: readonly string[];
  query: string;
  variables: Record<string, unknown>;
}

/**
 * Answer one root field (the operation Hasura and refine name: `notes`,
 * `notes_by_pk`, `insert_notes_one`, an action mutation) from its arguments,
 * resolved against the request's variables. Throw a `GraphQLError` to answer
 * the request with that error.
 */
export type GraphQLWireOperation = (args: Record<string, unknown>, request: GraphQLWireRequest) => unknown;

function jsonResponse(data: unknown): Response {
  return new Response(JSON.stringify(data), { headers: { "content-type": "application/json" } });
}

function unhandled(message: string): never {
  // The transport reports a rejected fetch only as "Request failed."; name the gap here.
  console.error(message);
  throw new Error(message);
}

async function answer(
  operations: Readonly<Record<string, GraphQLWireOperation>>,
  init: RequestInit | undefined,
  onRequest: ((request: GraphQLWireRequest) => void) | undefined,
): Promise<Response> {
  const body = JSON.parse(String(init?.body ?? "{}")) as { query?: string; variables?: Record<string, unknown> | null; operationName?: string | null };
  const query = body.query ?? "";
  const variables = body.variables ?? {};
  let document: DocumentNode;
  try {
    document = parse(query);
  } catch (error) {
    unhandled(`GraphQL wire fake: the request is not a GraphQL document (${String(error)}).`);
  }
  const operation = document.definitions.find((definition): definition is OperationDefinitionNode =>
    definition.kind === Kind.OPERATION_DEFINITION && (!body.operationName || definition.name?.value === body.operationName));
  if (!operation) unhandled(`GraphQL wire fake: the request names no operation it carries (${body.operationName ?? "anonymous"}).`);
  const roots = operation.selectionSet.selections.filter((selection): selection is FieldNode => selection.kind === Kind.FIELD);
  const request: GraphQLWireRequest = { operationName: operation.name?.value, fields: roots.map((field) => field.name.value), query, variables };
  onRequest?.(request);
  const label = request.operationName ?? request.fields.join(", ");
  for (const definition of operation.variableDefinitions ?? []) {
    const name = definition.variable.name.value;
    if (definition.type.kind === Kind.NON_NULL_TYPE && !definition.defaultValue && variables[name] == null) {
      unhandled(`GraphQL wire fake: ${label} omits its required variable $${name}.`);
    }
  }
  const missing = request.fields.filter((field) => !Object.hasOwn(operations, field));
  if (missing.length) unhandled(`GraphQL wire fake: no handler answers ${missing.join(", ")} (${label}).`);
  try {
    const data = await Promise.all(roots.map(async (field) => [
      field.alias?.value ?? field.name.value,
      await operations[field.name.value]!(Object.fromEntries((field.arguments ?? []).map((argument) =>
        [argument.name.value, valueFromASTUntyped(argument.value, variables)])), request),
    ] as const));
    return jsonResponse({ data: Object.fromEntries(data) });
  } catch (error) {
    if (error instanceof GraphQLError) return jsonResponse({ data: null, errors: [error.toJSON()] });
    throw error;
  }
}

/**
 * The GraphQL wire fake shared by stories and tests: a `fetch` for an Angee
 * GraphQL transport that parses each request and answers every root field
 * from the handler named for it, and answers the session's CSRF probe. A root
 * field without a handler, an unparseable document, or a required variable the
 * request omits fails loudly. `onRequest` observes each parsed request. It
 * imports no test runner, so Storybook bundles it as well as Vitest.
 */
export function graphqlWireFetch(
  operations: Readonly<Record<string, GraphQLWireOperation>>,
  onRequest?: (request: GraphQLWireRequest) => void,
): typeof globalThis.fetch {
  return (input, init) =>
    String(input instanceof Request ? input.url : input).includes("/auth/csrf/")
      ? Promise.resolve(jsonResponse({ token: "test" }))
      : answer(operations, init, onRequest);
}
