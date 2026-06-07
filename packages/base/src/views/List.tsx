import * as React from "react";
import type { Row } from "@angee/sdk";

import {
  ListView,
  type ListViewProps,
} from "./ListView";
import type {
  DataViewDefaultGroups,
  DataViewGroup,
} from "./data-view-model";
import {
  PAGE_ELEMENT_SLOT,
  parsePageColumns,
  requirePageColumns,
  requirePageModel,
} from "./page";

export type ListComponent<TRow extends Row = Row> = React.ComponentType<
  ListViewProps<TRow> & {
    defaultGroup?: DataViewGroup | null;
    defaultGroups?: DataViewDefaultGroups;
  }
>;

/**
 * Declarative list view.
 *
 * Used standalone, `List` renders the collection surface directly through
 * `ListView` or the supplied list renderer. Used as a `DataPage` child, the
 * element is parsed as a view declaration and `DataPage` stitches it into the
 * collection-record page. Export and reuse element constants directly; wrapper
 * components hide the marker from the parser.
 */
export interface ListProps<TRow extends Row = Row>
  extends Omit<ListViewProps<TRow>, "model" | "columns"> {
  /**
   * Model label rendered by this list, e.g. `"notes.Note"`.
   *
   * Required when rendered standalone. When nested inside `DataPage`, this may
   * be omitted and is inherited from the page; if both are declared, they must
   * match.
   */
  model?: string;
  /** Column element declarations for this list. */
  children?: React.ReactNode;
  /** Group seeded by grouping-capable list renderers. */
  defaultGroup?: DataViewGroup | null;
  /** Per-view group defaults seeded by grouping-capable list renderers. */
  defaultGroups?: DataViewDefaultGroups;
  /** Collection renderer. Defaults to `ListView`; pass `GroupListView` for grouping. */
  list?: ListComponent<TRow>;
}

function ListComponentImpl<TRow extends Row = Row>({
  model,
  children,
  list: Collection = ListView as ListComponent<TRow>,
  ...props
}: ListProps<TRow>): React.ReactElement {
  const resolvedModel = requirePageModel("List", model);
  const columns = requirePageColumns(
    "List",
    parsePageColumns<TRow>(children),
  );

  return (
    <Collection
      {...props}
      model={resolvedModel}
      columns={columns}
    />
  );
}

/**
 * Render a reusable list declaration standalone, or hand the same element to
 * `DataPage` for page-level composition. Element constants are the reuse unit;
 * wrapper components hide the marker from the parser.
 */
export const List = Object.assign(ListComponentImpl, {
  [PAGE_ELEMENT_SLOT]: "list" as const,
});
