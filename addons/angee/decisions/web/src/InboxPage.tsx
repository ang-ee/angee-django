import { useNavigate } from "@tanstack/react-router";
import type { ReactElement } from "react";
import {
  Column, List, LoadingPanel, ResourceList, Select,
  routeSearchParam, updateRouteSearch,
  useRouteHref, useRouteSearch, useRuntimeAuth, type StringIdRow,
} from "@angee/ui";

import { useDecisionsT } from "./i18n";
import { DecisionSubject } from "./DecisionSubject";

const MODEL = "decisions.Decision";

/** Personal seats and requests; resource and route owners retain all collection state. */
export function InboxPage(): ReactElement {
  const t = useDecisionsT();
  const { user } = useRuntimeAuth();
  const search = useRouteSearch();
  const navigate = useNavigate();
  const routeHref = useRouteHref();
  const scope = routeSearchParam(search, "decisionScope") === "requested" ? "requested" : "assigned";
  const state = routeSearchParam(search, "decisionState") === "settled" ? "settled" : "open";
  if (!user) return <LoadingPanel />;

  return (
    <ResourceList<StringIdRow>
      resource={MODEL}
      hideCreate
      baseFilter={{
        [scope === "requested" ? "requester" : "assignees"]: { exact: user.id },
        is_open: { exact: state === "open" },
      }}
      fields={["record_model_label"]}
      order={{ created_at: "DESC" }}
      rowHref={(row) => routeHref("decisions.inbox.record", { id: row.id })}
      emptyContent={t("inbox.empty")}
      toolbarActions={<>
        <Select
          aria-label={t("inbox.scope")}
          value={scope}
          options={[
            { value: "assigned", label: t("inbox.assigned") },
            { value: "requested", label: t("inbox.requested") },
          ]}
          onValueChange={(value) => void navigate({
            to: ".", search: updateRouteSearch({ decisionScope: value, page: undefined }),
          })}
        />
        <Select
          aria-label={t("inbox.state")}
          value={state}
          options={[
            { value: "open", label: t("inbox.open") },
            { value: "settled", label: t("inbox.settled") },
          ]}
          onValueChange={(value) => void navigate({
            to: ".", search: updateRouteSearch({ decisionState: value, page: undefined }),
          })}
        />
      </>}
    >
      <List resource={MODEL}>
        <Column field="kind" header={t("inbox.kind")} />
        <Column field="record_public_id" header={t("inbox.subject")} render={(row) => {
          const id = row.record_public_id;
          if (typeof id !== "string") return null;
          return typeof row.record_model_label === "string" ? <DecisionSubject model={row.record_model_label} id={id} /> : id;
        }} />
        <Column field="requester.display_name" header={t("inbox.requester")} />
        <Column field="expires_at" header={t("inbox.expiresAt")} />
        <Column field="verdict" header={t("inbox.verdict")} widget="statusBadge" />
      </List>
    </ResourceList>
  );
}
