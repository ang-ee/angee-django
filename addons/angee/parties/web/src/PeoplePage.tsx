import * as React from "react";
import { useAuthoredQuery } from "@angee/refine";
import { Alert, Column, List, LoadingPanel, PaneLabel, PrimaryPanePublisher, ResourceList, TreeView, errorMessage, useAuthoredResourceMutation, useLatestRef, useRouteSearch, useToast, updateRouteSearch, type DndPayload, type StringIdRow } from "@angee/ui";
import { useNavigate } from "@tanstack/react-router";
import {
  AddCircleMember,
  CIRCLE_MEMBER_INVALIDATES,
  PeopleWorkbench,
} from "./documents";
import { usePartiesT } from "./i18n";
import { personForm } from "./PersonForm";

const MODEL = "parties.Person";

/**
 * People (the person-kind contacts): a circle/smart-view workbench around the
 * shared create/edit/list/detail surface. The primary pane owns scope selection
 * and circle drops; the list keeps its generated facets and generic record
 * behavior; the registered Person form carries the record tabs and verbs.
 */
export function PeoplePage(): React.ReactElement {
  const t = usePartiesT();
  const navigate = useNavigate();
  const search = useRouteSearch();
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
      void navigate({
        to: ".",
        search: updateRouteSearch({
          peopleScope: row.id === "ALL" ? undefined : row.id,
          peopleCircle: undefined,
        }),
      });
    },
    [navigate],
  );
  const selectCircle = React.useCallback(
    (row: CircleTreeRow) => {
      void navigate({
        to: ".",
        search: updateRouteSearch({
          peopleScope: "CIRCLE",
          peopleCircle: row.id,
        }),
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
      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-auto p-2">
        <section>
          <PaneLabel>{t("people.smart.heading")}</PaneLabel>
          <TreeView<SmartViewRow>
            rows={smartViews}
            label="name"
            badge="count"
            selectedId={scope.kind === "CIRCLE" ? undefined : scope.kind}
            onSelect={selectSmartView}
          />
        </section>
        <section className="flex min-h-0 flex-col">
          <PaneLabel>{t("people.circles.heading")}</PaneLabel>
          {workbench.isFetching && circles.length === 0 ? (
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
    [circles, dropPerson, scope, selectCircle, selectSmartView, smartViews, t, workbench.data?.people_workbench.truncated, workbench.isFetching],
  );
  const filteredIds = workbench.data?.people_workbench.filtered_ids;
  const baseFilter =
    scope.kind === "ALL"
      ? undefined
      : { id: { inList: workbench.isFetching ? [] : (filteredIds ?? []) } };
  return (
    <>
      <PrimaryPanePublisher node={primaryPane} />
      <ResourceList<PersonRow>
        resource={MODEL}
        form={personForm}
        placement="inline"
        routed
        baseFilter={baseFilter}
        draggableRow={personDragPayload}
      >
        <List resource={MODEL}>
          <Column field="display_name" />
          <Column field="circle_names" header={t("people.circles.heading")} />
          <Column field="given_name" />
          <Column field="family_name" />
          <Column field="created_at" />
          <Column field="tags" hiddenByDefault />
        </List>
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
