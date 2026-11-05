import {
  parse,
  parseType,
  print,
} from "graphql";
import { refineFieldsFromPaths, selectionText } from "./selections";
import type { TypedDocumentNode } from "./typed-document";

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

export interface GroupedListQueryTarget extends ListQueryTarget {
  groupedRowsRoot: string;
  groupByType: string;
  groupedRowPageType: string;
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

/** Build one operation for independently paged list scopes of the same resource. */
export function groupedListQueryMeta(
  target: GroupedListQueryTarget,
  paths: readonly string[],
  groupBy: { field: string; granularity?: string },
  scopes: readonly {
    value: unknown;
    page: number;
    pageSize: number;
  }[],
  where?: Record<string, unknown>,
  orderBy?: unknown,
) {
  for (const name of [target.groupedRowsRoot, target.groupByType, target.groupedRowPageType, target.filterType, target.orderType]) {
    if (!/^[_A-Za-z][_0-9A-Za-z]*$/.test(name)) throw new Error(`Invalid GraphQL name: ${name}`);
  }
  const fields = refineFieldsFromPaths(paths);
  if (fields.length === 0) throw new Error("A grouped list query requires a selection.");
  const document: TypedDocumentNode<Record<string, unknown>, Record<string, unknown>> = parse(`
    query GroupedResourceList(
      $group_by: ${target.groupByType}!,
      $pages: [${target.groupedRowPageType}!]!,
      $where: ${target.filterType},
      $order_by: [${target.orderType}!]
    ) {
      rows: ${target.groupedRowsRoot}(group_by: $group_by, pages: $pages, where: $where, order_by: $order_by) {
        ${selectionText(fields, {})}
      }
    }
  `);
  return {
    document,
    variables: {
      group_by: groupBy,
      pages: scopes.map(({ value, page, pageSize }) => ({ key: value, limit: pageSize, offset: (page - 1) * pageSize })),
      ...(where === undefined ? {} : { where }),
      ...(orderBy === undefined ? {} : { order_by: orderBy }),
    },
  };
}
