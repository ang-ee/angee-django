import * as React from "react";
import { ListView, type ListColumn, type RecordPanelContext, type StringIdRow } from "@angee/ui";

import { usePartiesT } from "./i18n";

interface RelationshipRow extends StringIdRow {
  party?: { id?: string; display_name?: string } | null;
  other_party?: { display_name?: string } | null;
  other_name?: string;
  kind?: { name?: string; inverse_name?: string } | null;
}

/**
 * Both readings of the person's typed edges in one list. A row anchored on this
 * card names its counterparty by the kind ("Mother: Jane", including free-text
 * relatives who are not directory entries); a row anchored on another card that
 * names this person reads through the kind's inverse label, falling back to the
 * forward name for symmetric kinds. The direction column tells them apart.
 */
export function PersonRelationshipsTab({ recordId }: Pick<RecordPanelContext, "recordId">): React.ReactElement {
  const t = usePartiesT();
  const columns = React.useMemo<readonly ListColumn<RelationshipRow>[]>(() => {
    const outgoing = (row: RelationshipRow) => row.party?.id === recordId;
    return [
      {
        field: "party.id",
        header: t("relationship.direction"),
        sortable: false,
        render: (row) => outgoing(row) ? t("relationship.direction.outgoing") : t("relationship.direction.incoming"),
      },
      {
        field: "kind.name",
        header: t("relationship.kind"),
        sortable: false,
        render: (row) => outgoing(row) ? row.kind?.name ?? "" : row.kind?.inverse_name || row.kind?.name || "",
      },
      {
        field: "other_party.display_name",
        header: t("relationship.person"),
        sortable: false,
        render: (row) => outgoing(row)
          ? row.other_party?.display_name || row.other_name || ""
          : row.party?.display_name ?? "",
      },
      { field: "started_at" },
      { field: "ended_at" },
    ];
  }, [recordId, t]);
  return (
    <ListView<RelationshipRow>
      resource="parties.Relationship"
      presentation="embedded"
      fields={[
        "id", "party.id", "party.display_name", "kind.name", "kind.inverse_name",
        "other_party.display_name", "other_name", "started_at", "ended_at",
      ]}
      baseFilter={{ OR: [{ party: { exact: recordId } }, { other_party: { exact: recordId } }] }}
      columns={columns}
      emptyContent={t("person.empty.relationships")}
    />
  );
}
