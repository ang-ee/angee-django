import * as React from "react";
import type {
  Row,
} from "@angee/metadata";

import {
  ListView,
  type ListViewProps,
} from "./ListView";
import type {
  ResourceViewDefaultGroups,
  ResourceViewGroup,
  ResourceViewKind,
} from "./resource-view-model";
import {
  PAGE_ELEMENT_SLOT,
  mergePageFacets,
  parsePageColumns,
  parsePageFacets,
  requirePageColumns,
  requirePageResource,
} from "./page";

export type ListComponent<TRow extends Row = Row> = React.ComponentType<
  ListViewProps<TRow>
>;

/**
 * Declarative list view.
 *
 * Used standalone, `List` renders the collection surface directly through
 * `ListView` or the supplied list renderer. Used as a `ResourceList` child, the
 * element is parsed as a view declaration and `ResourceList` stitches it into the
 * collection-record page. Export and reuse element constants directly; wrapper
 * components hide the marker from the parser.
 */
export interface ListProps<TRow extends Row = Row>
  extends Omit<ListViewProps<TRow>, "resource" | "columns"> {
  /**
   * Resource rendered by this list, e.g. `"notes.Note"`.
   *
   * Required when rendered standalone. When nested inside `ResourceList`, this may
   * be omitted and is inherited from the page; if both are declared, they must
   * match.
   */
  resource?: string;
  /** Column and facet element declarations for this list. */
  children?: React.ReactNode;
  /** Initial collection view for the resource list. */
  defaultView?: ResourceViewKind;
  /** Group seeded by the resource list. */
  defaultGroup?: ResourceViewGroup | null;
  /** Per-view group defaults seeded by the resource list. */
  defaultGroups?: ResourceViewDefaultGroups;
  /** Collection renderer. Defaults to the grouped-capable `ListView`. */
  list?: ListComponent<TRow>;
}

function ListComponentImpl<TRow extends Row = Row>({
  resource,
  children,
  facets: explicitFacets,
  list: Collection = ListView as ListComponent<TRow>,
  ...props
}: ListProps<TRow>): React.ReactElement {
  const resolvedResource = requirePageResource("List", resource);
  const columns = requirePageColumns(
    "List",
    parsePageColumns<TRow>(children),
  );
  const facets = mergePageFacets(explicitFacets, parsePageFacets(children));

  return (
    <Collection
      {...props}
      resource={resolvedResource}
      columns={columns}
      facets={facets}
    />
  );
}

/**
 * Render a reusable list declaration standalone, or hand the same element to
 * `ResourceList` for page-level composition. Element constants are the reuse unit;
 * wrapper components hide the marker from the parser.
 */
export const List = Object.assign(ListComponentImpl, {
  [PAGE_ELEMENT_SLOT]: "list" as const,
});
