import * as React from "react";
import {
  Button,
  Glyph,
  ListView,
  MutationDialog,
  errorMessage,
  useAuthoredResourceMutation,
  useConfirm,
  useToast,
  type ListColumn,
  type MutationDialogField,
  type StringIdRow,
} from "@angee/ui";

import {
  AddCircleMember,
  CIRCLE_MEMBER_INVALIDATES,
  RemoveCircleMember,
} from "./documents";
import { usePartiesT } from "./i18n";

type MembershipRow = StringIdRow;

/** Actionable circle list for one person's detail tab. */
export function PersonCirclesList({ personId }: { personId: string }): React.ReactElement {
  return <CircleMembershipList anchor="person" anchorId={personId} />;
}

/** Actionable member list for one circle's detail tab. */
export function CircleMembersList({ circleId }: { circleId: string }): React.ReactElement {
  return <CircleMembershipList anchor="circle" anchorId={circleId} />;
}

function CircleMembershipList({
  anchor,
  anchorId,
}: {
  anchor: "circle" | "person";
  anchorId: string;
}): React.ReactElement {
  const t = usePartiesT();
  const confirm = useConfirm();
  const toast = useToast();
  const [addOpen, setAddOpen] = React.useState(false);
  const [add, addState] = useAuthoredResourceMutation(AddCircleMember, {
    invalidateModels: CIRCLE_MEMBER_INVALIDATES,
  });
  const [remove, removeState] = useAuthoredResourceMutation(RemoveCircleMember, {
    invalidateModels: CIRCLE_MEMBER_INVALIDATES,
  });
  const busy = addState.fetching || removeState.fetching;

  const fields = React.useMemo<readonly MutationDialogField[]>(
    () => [
      anchor === "person"
        ? {
            name: "circle",
            label: t("person.circleName"),
            required: true,
            relation: { resource: "parties.Circle", labelField: "name" },
          }
        : {
            name: "party",
            label: t("circle.memberParty"),
            required: true,
            relation: { resource: "parties.Person", labelField: "display_name" },
          },
    ],
    [anchor, t],
  );
  const columns = React.useMemo<readonly ListColumn<MembershipRow>[]>(
    () => [
      anchor === "person"
        ? { field: "circle.name", header: t("person.circleName") }
        : { field: "party.display_name", header: t("circle.memberParty") },
      { field: "source" },
      { field: "confidence" },
      {
        field: "id",
        header: t("circle.membership.actions"),
        headerVisuallyHidden: true,
        sortable: false,
        align: "right",
        render: (row) => (
          <Button
            type="button"
            variant="ghost"
            size="iconSm"
            aria-label={t("circle.membership.remove")}
            title={t("circle.membership.remove")}
            disabled={busy}
            onClick={(event) => {
              event.stopPropagation();
              void removeMembership(row);
            }}
          >
            <Glyph decorative name="trash" />
          </Button>
        ),
      },
    ],
    [anchor, busy, t],
  );

  async function removeMembership(row: MembershipRow): Promise<void> {
    const accepted = await confirm({
      title: t("circle.membership.removeTitle"),
      body: t("circle.membership.removeDescription"),
      confirm: t("circle.membership.remove"),
      danger: true,
    });
    if (!accepted) return;
    try {
      await remove({ id: row.id });
    } catch (cause) {
      toast.danger({
        title: t("circle.membership.removeError"),
        description: errorMessage(cause, t("circle.membership.removeError")),
      });
    }
  }

  return (
    <>
      <ListView<MembershipRow>
        resource="parties.CircleMember"
        scope="local"
        fields={[
          "id",
          anchor === "person" ? "circle.name" : "party.display_name",
          "source",
          "confidence",
        ]}
        baseFilter={{
          [anchor === "person" ? "party" : "circle"]: { exact: anchorId },
        }}
        columns={columns}
        toolbarActions={
          <Button type="button" variant="primary" size="sm" onClick={() => setAddOpen(true)}>
            <Glyph decorative name="plus" />
            {anchor === "person"
              ? t("circle.membership.addCircle")
              : t("circle.membership.addPerson")}
          </Button>
        }
        emptyContent={
          anchor === "person" ? t("person.empty.circles") : t("circle.empty.members")
        }
      />
      <MutationDialog
        open={addOpen}
        onOpenChange={setAddOpen}
        title={
          anchor === "person"
            ? t("circle.membership.addCircle")
            : t("circle.membership.addPerson")
        }
        fields={fields}
        submitLabel={t("circle.membership.add")}
        submittingLabel={t("circle.membership.adding")}
        errorFallback={t("circle.membership.addError")}
        onSubmit={(values) =>
          add({
            circle: anchor === "circle" ? anchorId : String(values.circle ?? ""),
            party: anchor === "person" ? anchorId : String(values.party ?? ""),
          })
        }
      />
    </>
  );
}
