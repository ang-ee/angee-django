import { parse } from "graphql";
import { refineFieldsFromPaths, selectionText } from "./selections";

/** Executable GraphQL names supplied by the resource metadata edge. */
export interface ListQueryTarget {
  root: string;
  aggregateRoot: string;
  filterType: string;
  orderType: string;
}

/** Bind already-compiled predicates through Refine's native document override. */
export function listQueryMeta(
  target: ListQueryTarget,
  paths: readonly string[],
  where?: Record<string, unknown>,
  orderBy?: unknown,
) {
  for (const name of [target.root, target.aggregateRoot, target.filterType, target.orderType]) {
    if (!/^[_A-Za-z][_0-9A-Za-z]*$/.test(name)) throw new Error(`Invalid GraphQL name: ${name}`);
  }
  const fields = refineFieldsFromPaths(paths);
  if (fields.length === 0) throw new Error("A list query requires a selection.");
  const aggregateAlias = `${target.root}_aggregate`;
  const aggregate = target.aggregateRoot === aggregateAlias
    ? aggregateAlias : `${aggregateAlias}: ${target.aggregateRoot}`;
  return {
    fields,
    gqlQuery: parse(`query ResourceList($where: ${target.filterType}, $order_by: [${target.orderType}!], $limit: Int, $offset: Int) {
      ${target.root}(where: $where, order_by: $order_by, limit: $limit, offset: $offset) { ${selectionText(fields)} }
      ${aggregate}(where: $where) { aggregate { count } }
    }`),
    gqlVariables: { ...(where === undefined ? {} : { where }), ...(orderBy === undefined ? {} : { order_by: orderBy }) },
  };
}
