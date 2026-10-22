import { parse, parseType, print } from "graphql";
import { refineFieldsFromPaths, selectionText } from "./selections";

/** Executable GraphQL names supplied by the resource metadata edge. */
export interface ListQueryTarget {
  root: string;
  aggregateRoot: string;
  filterType: string;
  orderType: string;
}

export interface ListQueryFieldArgument {
  name: string;
  /** GraphQL input type as emitted in SDL, for example `ID!`. */
  type: string;
  value: unknown;
}

/** Arguments bound to one selected field while retaining the field's response name. */
export interface ListQueryFieldBinding {
  path: string;
  arguments: readonly ListQueryFieldArgument[];
}

/** Bind already-compiled predicates through Refine's native document override. */
export function listQueryMeta(
  target: ListQueryTarget,
  paths: readonly string[],
  where?: Record<string, unknown>,
  orderBy?: unknown,
  fieldBindings: readonly ListQueryFieldBinding[] = [],
) {
  for (const name of [target.root, target.aggregateRoot, target.filterType, target.orderType]) {
    if (!/^[_A-Za-z][_0-9A-Za-z]*$/.test(name)) throw new Error(`Invalid GraphQL name: ${name}`);
  }
  const fields = refineFieldsFromPaths(paths);
  if (fields.length === 0) throw new Error("A list query requires a selection.");
  const variableDefinitions = [
    `$where: ${target.filterType}`,
    `$order_by: [${target.orderType}!]`,
    "$limit: Int",
    "$offset: Int",
  ];
  const variables: Record<string, unknown> = {
    ...(where === undefined ? {} : { where }),
    ...(orderBy === undefined ? {} : { order_by: orderBy }),
  };
  const argumentsByPath: Record<string, string> = {};
  for (const [bindingIndex, binding] of fieldBindings.entries()) {
    if (!paths.includes(binding.path)) {
      throw new Error(`Bound GraphQL field is not selected: ${binding.path}`);
    }
    if (argumentsByPath[binding.path] !== undefined) {
      throw new Error(`GraphQL field is bound more than once: ${binding.path}`);
    }
    if (binding.arguments.length === 0) {
      throw new Error(`GraphQL field binding has no arguments: ${binding.path}`);
    }
    const argumentText = binding.arguments.map((argument, argumentIndex) => {
      if (!/^[_A-Za-z][_0-9A-Za-z]*$/.test(argument.name)) {
        throw new Error(`Invalid GraphQL argument name: ${argument.name}`);
      }
      if (argument.value === undefined) {
        throw new Error(`GraphQL argument value is undefined: ${binding.path}.${argument.name}`);
      }
      const variable = `selection_${bindingIndex}_${argumentIndex}`;
      variableDefinitions.push(`$${variable}: ${print(parseType(argument.type))}`);
      variables[variable] = argument.value;
      return `${argument.name}: $${variable}`;
    });
    argumentsByPath[binding.path] = `(${argumentText.join(", ")})`;
  }
  const aggregateAlias = `${target.root}_aggregate`;
  const aggregate = target.aggregateRoot === aggregateAlias
    ? aggregateAlias : `${aggregateAlias}: ${target.aggregateRoot}`;
  return {
    fields,
    gqlQuery: parse(`query ResourceList(${variableDefinitions.join(", ")}) {
      ${target.root}(where: $where, order_by: $order_by, limit: $limit, offset: $offset) { ${selectionText(fields, argumentsByPath)} }
      ${aggregate}(where: $where) { aggregate { count } }
    }`),
    gqlVariables: variables,
  };
}
