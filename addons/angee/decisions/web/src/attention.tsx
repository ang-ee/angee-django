import { rowValueAtPath, type Row } from "@angee/metadata";
import { Badge, type ListColumn, type ResourceToolbarFilterOption } from "@angee/ui";

/** Opt in by adding this column; its field selects the server's derived boolean. */
export function decisionAttentionColumn<TRow extends Row>(): ListColumn<TRow> {
  return {
    field: "has_open_decisions", header: "Attention", sortable: false,
    render: (row) => rowValueAtPath(row, "has_open_decisions") === true
      ? <Badge tone="warning" density="compact">Needs attention</Badge> : null,
  };
}

export const openDecisionFilter: ResourceToolbarFilterOption = {
  id: "open-decisions", label: "Needs attention", filter: { has_open_decisions: { exact: true } },
};
