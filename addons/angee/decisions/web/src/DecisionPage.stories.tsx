import { useMemo } from "react";
import * as v from "valibot";
import { operationDocuments } from "@angee/gql/console/actions";
import { RoutedRuntimeFixture, jsonResponse, storySchema, testDataResource } from "@angee/storybook/testing";
import { createRouteHref, isJsonObject, JsonValueSchema, useRouteParam } from "@angee/ui";

import { DecisionPage } from "./DecisionPage";
import { InboxPage } from "./InboxPage";
import { DECISION_MODEL } from "./documents.console";
import { decisionFixture, decisionResourceFixture, decisionSeatFixture, decisionSubjectFixture } from "./testing";

export default { title: "Decisions/Decision page", parameters: { layout: "fullscreen" } };
export const Open = { render: () => <DecisionStory /> };
export const Settled = { render: () => <DecisionStory settled /> };
export const Inbox = { render: () => <DecisionStory inbox /> };
export const Conflict = { render: () => <DecisionStory conflict /> };

const RequestSchema = v.object({ query: v.string(), variables: v.optional(v.record(v.string(), JsonValueSchema), {}) });
const documents = { console: operationDocuments };
const runtime = {
  routeHref: createRouteHref([{ name: "decisions.inbox", path: "/decisions" }, { name: "decisions.inbox.record", path: "/decisions/$id" }]),
  routesByResource: { [DECISION_MODEL]: { collection: "decisions.inbox", record: { name: "decisions.inbox.record", param: "id" } } },
  auth: { user: { id: "usr_reviewer", name: "Reviewer" }, status: "authenticated" as const, hasRole: () => false },
};

function DecisionRoute() { return useRouteParam("id") ? <DecisionPage /> : <InboxPage />; }

function DecisionStory({ settled = false, inbox = false, conflict = false }: { settled?: boolean; inbox?: boolean; conflict?: boolean }) {
  const schemas = useMemo(() => {
    let conflicting = conflict;
    let current = decisionFixture({ context: { facts: [{ pointer: "/reference", label: "Reference", value: "R-7", authority: "source" }], references: [] } });
    if (settled) current = { ...current, is_open: false, can_act: false, verdict: "COMPLETED", closed_reason: "RESOLVED", resolution: { action: "accept", note: "Already reviewed", reference: "R-7" },
      resolved_by: { display_name: "Reviewer" }, resolved_at: "2026-09-29T09:30:00Z" };
    const fixture = storySchema(async (_input, init) => {
      const { query, variables } = v.parse(RequestSchema, JSON.parse(String(init?.body ?? "{}")));
      if (query.includes("decide(")) {
        if (conflicting) { current = { ...current, revision: current.revision + 1 }; conflicting = false; }
        if (variables.revision !== current.revision) return jsonResponse({ data: { decide: {
          ok: false, message: "The decision has changed.", validation_errors: { revision: ["Reload the decision."] },
        } } });
        const action = variables.action;
        if ((action !== "accept" && action !== "reject") || !isJsonObject(variables.values)) throw new Error("Unexpected story decision payload.");
        current = { ...current, is_open: false, can_act: false, revision: current.revision + 1, verdict: action === "reject" ? "REJECTED" : "COMPLETED", closed_reason: "RESOLVED",
          resolution: { action, ...variables.values }, resolved_by: { display_name: "Reviewer" }, resolved_at: "2026-09-29T09:30:00Z" };
        return jsonResponse({ data: { decide: { ok: true, message: "Decision recorded.", id: current.id } } });
      }
      if (query.includes("DecisionDetail")) return jsonResponse({ data: { decisions_by_pk: current } });
      if (query.includes("DecisionSeats")) return jsonResponse({ data: { decisions: [decisionSeatFixture({
        id: current.id, verdict: current.verdict, closed_reason: current.closed_reason,
      })] } });
      if (query.includes("notes_by_pk")) return jsonResponse({ data: { notes_by_pk: { id: "nte_7", display_name: "Review notes" } } });
      return jsonResponse({ data: { decisions: [current], decisions_aggregate: { aggregate: { count: 1 } } } });
    }).public!;
    return { public: fixture, console: { ...fixture, metadata: { angee: { resources: [
      decisionResourceFixture,
      testDataResource("decisions.DecisionGroup", { capabilities: ["list", "detail"], roots: { list: "decision_groups", detail: "decision_groups_by_pk" } }),
      decisionSubjectFixture,
    ] } } } };
  }, [settled, conflict]);
  return <RoutedRuntimeFixture activeSchema="console" schemas={schemas} collectionPath="/decisions" initialEntry={inbox ? "/decisions" : "/decisions/dcn_review"}
    runtime={runtime} resourceName={DECISION_MODEL} resourceLabel="Decisions" operationDocuments={documents}>
    <DecisionRoute />
  </RoutedRuntimeFixture>;
}
