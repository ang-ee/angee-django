import * as React from "react";
import { useAuthoredQuery } from "@angee/refine";
import {
  Action,
  Alert,
  Column,
  Facet,
  Field,
  Form,
  Group,
  List,
  ListView,
  LoadingPanel,
  PrimaryPanePublisher,
  ResourceList,
  SectionEyebrow,
  TreeView,
  errorMessage,
  useAuthoredResourceMutation,
  useLatestRef,
  useToast,
  type ActionDescriptor,
  type DndPayload,
  type ListColumn,
  type RecordPanelContext,
  type RecordTabDescriptor,
  type StringIdRow,
} from "@angee/ui";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { PersonCirclesList } from "./CircleMembershipList";
import {
  AddCircleMember,
  CIRCLE_MEMBER_INVALIDATES,
  PeopleWorkbench,
} from "./documents";
import { IdentityTab } from "./IdentityTab";
import { usePartiesT } from "./i18n";
import { partyMergePath } from "./routes";

const MODEL = "parties.Person";

type RelatedRow = StringIdRow;

function handleColumns(t: ReturnType<typeof usePartiesT>): readonly ListColumn<RelatedRow>[] {
  return [
    { field: "platform" },
    { field: "value", render: (row) => <span className="font-medium text-fg">{String(row.value ?? "")}</span> },
    { field: "label" },
    {
      field: "is_preferred",
      header: t("person.handlePreferred"),
      render: (row) => (row.is_preferred ? t("common.yes") : ""),
    },
  ];
}

const addressColumns: readonly ListColumn<RelatedRow>[] = [
  { field: "label" },
  { field: "street", render: (row) => <span className="font-medium text-fg">{String(row.street ?? "")}</span> },
  { field: "city" },
  { field: "region" },
  { field: "country" },
];

/**
 * One related collection on the Person detail — the person's handles or addresses — a local-scoped ListView filtered to this party, the same
 * shared list primitive the routed pages use (toolbar/empty/error affordances).
 */
function PartyRelatedTab({
  recordId,
  resource,
  fields,
  columns,
  emptyContent,
}: RecordPanelContext & {
  resource: string;
  fields: readonly string[];
  columns: readonly ListColumn<RelatedRow>[];
  emptyContent: string;
}): React.ReactElement {
  return (
    <ListView<RelatedRow>
      resource={resource}
      scope="local"
      fields={fields}
      baseFilter={{ party: { exact: recordId } }}
      columns={columns}
      emptyContent={emptyContent}
    />
  );
}

/**
 * Both readings of the person's typed edges in one tab: rows anchored on this
 * card (the counterparty is their *kind* — "Mother: Jane", including free-text
 * relatives who are not directory entries) and rows anchored on other cards
 * that name this person (rendered through the kind's inverse label, falling
 * back to the forward name for symmetric kinds).
 */
function RelationshipsTab({ recordId }: RecordPanelContext): React.ReactElement {
  const t = usePartiesT();
  const anchoredColumns = React.useMemo<readonly ListColumn<RelatedRow>[]>(
    () => [
      { field: "kind.name", header: t("relationship.kind") },
      {
        field: "other_party.display_name",
        header: t("relationship.person"),
        render: (row) => {
          const typed = row as { other_party?: { display_name?: string } | null; other_name?: string };
          return <>{typed.other_party?.display_name || typed.other_name || ""}</>;
        },
      },
      { field: "started_at" },
      { field: "ended_at" },
    ],
    [t],
  );
  const inboundColumns = React.useMemo<readonly ListColumn<RelatedRow>[]>(
    () => [
      {
        field: "kind.name",
        header: t("relationship.kind"),
        render: (row) => {
          const kind = (row as { kind?: { name?: string; inverse_name?: string } }).kind;
          return <>{kind?.inverse_name || kind?.name || ""}</>;
        },
      },
      { field: "party.display_name", header: t("relationship.person") },
      { field: "started_at" },
      { field: "ended_at" },
    ],
    [t],
  );
  return (
    <div className="flex flex-col gap-4">
      <ListView<RelatedRow>
        resource="parties.Relationship"
        scope="local"
        fields={["id", "kind.name", "other_party.display_name", "other_name", "started_at", "ended_at"]}
        baseFilter={{ party: { exact: recordId } }}
        columns={anchoredColumns}
        emptyContent={t("person.empty.relationships")}
      />
      <ListView<RelatedRow>
        resource="parties.Relationship"
        scope="local"
        fields={["id", "kind.name", "kind.inverse_name", "party.display_name", "started_at", "ended_at"]}
        baseFilter={{ other_party: { exact: recordId } }}
        columns={inboundColumns}
        emptyContent={t("person.empty.inboundRelationships")}
      />
    </div>
  );
}

function personRecordTabs(
  t: ReturnType<typeof usePartiesT>,
): readonly RecordTabDescriptor[] {
  return [
    {
      id: "handles",
      label: t("person.tabs.handles"),
      render: (context) => (
        <PartyRelatedTab
          {...context}
          resource="parties.Handle"
          fields={["id", "platform", "value", "label", "is_preferred"]}
          columns={handleColumns(t)}
          emptyContent={t("person.empty.handles")}
        />
      ),
    },
    {
      id: "identity",
      label: t("person.tabs.identity"),
      render: (context) => <IdentityTab {...context} />,
    },
    {
      id: "circles",
      label: t("person.tabs.circles"),
      render: ({ recordId }) => <PersonCirclesList personId={recordId} />,
    },
    {
      id: "relationships",
      label: t("person.tabs.relationships"),
      render: (context) => <RelationshipsTab {...context} />,
    },
    {
      id: "addresses",
      label: t("person.tabs.addresses"),
      render: (context) => (
        <PartyRelatedTab
          {...context}
          resource="parties.Address"
          fields={["id", "label", "street", "city", "region", "postal_code", "country"]}
          columns={addressColumns}
          emptyContent={t("person.empty.addresses")}
        />
      ),
    },
  ];
}

function peopleForm(
  t: ReturnType<typeof usePartiesT>,
  mergeSubmit: NonNullable<ActionDescriptor["submit"]>,
): React.ReactElement {
  return (
    <Form resource={MODEL}>
      <Field name="display_name" title />
      <Group label={t("person.group.name")} columns={2}>
        <Field name="given_name" label={t("person.field.givenName")} />
        <Field name="family_name" label={t("person.field.familyName")} />
        <Field name="additional_name" label={t("person.field.middleName")} />
        <Field name="nickname" label={t("person.field.nickname")} />
        <Field name="name_prefix" label={t("person.field.prefix")} />
        <Field name="name_suffix" label={t("person.field.suffix")} />
      </Group>
      <Group label={t("person.group.details")} columns={2}>
        <Field name="birthday" label={t("person.field.birthday")} />
        <Field name="anniversary" label={t("person.field.anniversary")} />
        <Field name="folder" label={t("person.folder")} readOnly />
      </Group>
      <Field name="notes" />
      <Action
        id="merge-into"
        label={t("person.action.merge")}
        args={[
          {
            name: "otherParty",
            argKind: "relation",
            resource: "parties.Party",
            label: t("person.action.mergeOther"),
            description: t("person.action.mergeOther.description"),
          },
        ]}
        visibleWhen={() => true}
        submit={mergeSubmit}
      />
    </Form>
  );
}

/**
 * People (the person-kind contacts): a circle/smart-view workbench around the
 * shared create/edit/list/detail surface. The primary pane owns scope selection
 * and circle drops; the list keeps its generated folder facet and generic record
 * behavior. Detail tabs carry identity, circle, relationship, and address
 * collections.
 */
export function PeoplePage(): React.ReactElement {
  const t = usePartiesT();
  const navigate = useNavigate();
  const search = useSearch({ strict: false }) as Readonly<Record<string, unknown>>;
  const toast = useToast();
  const scope = React.useMemo(
    () => peopleScopeFromSearch(search),
    [search.peopleCircle, search.peopleScope],
  );
  const variables = React.useMemo(
    () => ({
      scope: scope.kind,
      circle: scope.kind === "CIRCLE" ? scope.circleId : null,
    }),
    [scope],
  );
  const workbench = useAuthoredQuery(PeopleWorkbench, variables, {
    models: CIRCLE_MEMBER_INVALIDATES,
  });
  const [addCircleMember] = useAuthoredResourceMutation(AddCircleMember, {
    invalidateModels: CIRCLE_MEMBER_INVALIDATES,
  });
  const dropRef = useLatestRef({ addCircleMember, t, toast });
  const tabs = React.useMemo(() => personRecordTabs(t), [t]);
  const mergeSubmit = React.useCallback<NonNullable<ActionDescriptor["submit"]>>(
    async (values, context) => {
      const recordId = typeof context.record?.id === "string" ? context.record.id : "";
      const otherId = typeof values.otherParty === "string" ? values.otherParty : "";
      if (!recordId || !otherId || recordId === otherId) {
        return {
          ok: false,
          message: t("person.action.mergeRequired"),
          validationErrors: { otherParty: [t("person.action.mergeRequired")] },
        };
      }
      await navigate({ to: partyMergePath(recordId, otherId) });
      return { ok: true, message: "" };
    },
    [navigate, t],
  );
  const circles = React.useMemo<readonly CircleTreeRow[]>(
    () =>
      (workbench.data?.people_workbench.circles ?? []).map((circle) => ({
        id: circle.id,
        name: circle.name,
        icon: circle.icon,
        memberCount: circle.member_count,
        parentId: circle.parent?.id ?? "",
      })),
    [workbench.data?.people_workbench.circles],
  );
  const smartViews = React.useMemo<readonly SmartViewRow[]>(
    () => [
      {
        id: "ALL",
        name: t("people.smart.all"),
        count: workbench.data?.people_workbench.all_count ?? 0,
      },
      {
        id: "UNASSIGNED",
        name: t("people.smart.unassigned"),
        count: workbench.data?.people_workbench.unassigned_count ?? 0,
      },
      {
        id: "TO_REVIEW",
        name: t("people.smart.toReview"),
        count: workbench.data?.people_workbench.to_review_count ?? 0,
      },
    ],
    [t, workbench.data?.people_workbench],
  );
  const selectSmartView = React.useCallback(
    (row: SmartViewRow) => {
      // Untyped navigation: the router glue's `as never` idiom (refine router canon).
      void navigate({
        search: ((current: Record<string, unknown>) => ({
          ...current,
          peopleScope: row.id === "ALL" ? undefined : row.id,
          peopleCircle: undefined,
        })) as never,
      });
    },
    [navigate],
  );
  const selectCircle = React.useCallback(
    (row: CircleTreeRow) => {
      void navigate({
        search: ((current: Record<string, unknown>) => ({
          ...current,
          peopleScope: "CIRCLE",
          peopleCircle: row.id,
        })) as never,
      });
    },
    [navigate],
  );
  const dropPerson = React.useCallback(
    (circleId: string, payload: DndPayload) => {
      const party = String((payload.data as { id?: unknown }).id ?? "");
      if (!party) return;
      void dropRef.current.addCircleMember({ circle: circleId, party }).catch((cause) => {
        dropRef.current.toast.danger({
          title: dropRef.current.t("circle.membership.addError"),
          description: errorMessage(
            cause,
            dropRef.current.t("circle.membership.addError"),
          ),
        });
      });
    },
    [dropRef],
  );
  const primaryPane = React.useMemo(
    () => (
      <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-auto p-3">
        <section>
          <SectionEyebrow as="h2" spacing="menu">
            {t("people.smart.heading")}
          </SectionEyebrow>
          <TreeView<SmartViewRow>
            rows={smartViews}
            label="name"
            badge="count"
            selectedId={scope.kind === "CIRCLE" ? undefined : scope.kind}
            onSelect={selectSmartView}
          />
        </section>
        <section className="flex min-h-0 flex-col">
          <SectionEyebrow as="h2" spacing="menu">
            {t("people.circles.heading")}
          </SectionEyebrow>
          {workbench.fetching && circles.length === 0 ? (
            <LoadingPanel message={t("people.circles.loading")} density="inline" />
          ) : (
            <TreeView<CircleTreeRow>
              rows={circles}
              parent="parentId"
              label="name"
              badge="memberCount"
              icon="icon"
              selectedId={scope.kind === "CIRCLE" ? scope.circleId : undefined}
              onSelect={selectCircle}
              dropAccept={PERSON_DND}
              onNodeDrop={dropPerson}
              emptyContent={t("people.circles.empty")}
            />
          )}
        </section>
        {workbench.data?.people_workbench.truncated ? (
          <Alert tone="warning">{t("people.smart.truncated")}</Alert>
        ) : null}
      </div>
    ),
    [circles, dropPerson, scope, selectCircle, selectSmartView, smartViews, t, workbench.data?.people_workbench.truncated, workbench.fetching],
  );
  const filteredIds = workbench.data?.people_workbench.filtered_ids;
  const baseFilter =
    scope.kind === "ALL"
      ? undefined
      : { id: { inList: workbench.fetching ? [] : (filteredIds ?? []) } };
  return (
    <>
      <PrimaryPanePublisher node={primaryPane} />
      <ResourceList<PersonRow>
        resource={MODEL}
        placement="inline"
        routed
        recordTabs={tabs}
        baseFilter={baseFilter}
        draggableRow={personDragPayload}
      >
        <List resource={MODEL}>
          <Facet field="folder" label={t("person.folder")} labelField="name" />
          <Column field="display_name" />
          <Column field="circle_names" header={t("people.circles.heading")} />
          <Column field="folder.name" header={t("person.folder")} />
          <Column field="given_name" />
          <Column field="family_name" />
          <Column field="created_at" />
        </List>
        {peopleForm(t, mergeSubmit)}
      </ResourceList>
    </>
  );
}

const PERSON_DND = "parties.person";

type PeopleScope =
  | { kind: "ALL" | "UNASSIGNED" | "TO_REVIEW" }
  | { kind: "CIRCLE"; circleId: string };

function peopleScopeFromSearch(search: Readonly<Record<string, unknown>>): PeopleScope {
  if (search.peopleScope === "CIRCLE" && typeof search.peopleCircle === "string") {
    return { kind: "CIRCLE", circleId: search.peopleCircle };
  }
  if (search.peopleScope === "UNASSIGNED" || search.peopleScope === "TO_REVIEW") {
    return { kind: search.peopleScope };
  }
  return { kind: "ALL" };
}

interface PersonRow extends StringIdRow {
  display_name?: string;
}

interface SmartViewRow extends Record<string, unknown> {
  id: "ALL" | "UNASSIGNED" | "TO_REVIEW";
  name: string;
  count: number;
}

interface CircleTreeRow extends Record<string, unknown> {
  id: string;
  name: string;
  icon: string;
  memberCount: number;
  parentId: string;
}

function personDragPayload(row: PersonRow): DndPayload {
  return { type: PERSON_DND, data: { id: row.id } };
}
