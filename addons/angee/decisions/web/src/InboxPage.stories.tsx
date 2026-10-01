import { useMemo } from "react";
import * as v from "valibot";
import { operationDocuments } from "@angee/gql/console/actions";
import { RoutedRuntimeFixture, jsonResponse, storySchema } from "@angee/storybook/testing";
import { createRouteHref, isJsonObject, JsonValueSchema } from "@angee/ui";

import { InboxPage } from "./InboxPage";
import { DECISION_MODEL } from "./documents.console";
import { decisionFixture, decisionGroupFixture, decisionResourceFixture, decisionSubjectFixture, decisionUserFixture } from "./testing";

export default { title: "Decisions/Inbox", parameters: { layout: "fullscreen" } };
export const Open = { render: () => <DecisionStory /> };
export const Settled = { render: () => <DecisionStory settled /> };
export const SettledWithoutFacts = { render: () => <DecisionStory settled emptyFacts /> };
export const PendingWithoutFacts = { render: () => <DecisionStory pendingEmpty /> };
export const SiblingClosed = { render: () => <DecisionStory siblingClosed /> };
export const Inbox = { render: () => <DecisionStory inbox /> };
export const Conflict = { render: () => <DecisionStory conflict /> };
export const InvalidAttempt = { render: () => <DecisionStory invalidAttempt /> };
export const ReadOnly = { render: () => <DecisionStory readOnly /> };

const RequestSchema = v.object({ query: v.string(), variables: v.optional(v.record(v.string(), JsonValueSchema), {}) });
const documents = { console: operationDocuments };
const runtime = {
  routeHref: createRouteHref([{ name: "decisions.inbox", path: "/decisions" }, { name: "decisions.inbox.record", path: "/decisions/$id" }, { name: "notes", path: "/notes" }, { name: "notes.record", path: "/notes/$id" }, { name: "iam.users", path: "/iam/users" }, { name: "iam.users.record", path: "/iam/users/$id" }]),
  routesByResource: {
    [DECISION_MODEL]: { collection: "decisions.inbox", record: { name: "decisions.inbox.record", param: "id" } },
    "notes.Note": { collection: "notes", record: { name: "notes.record", param: "id" } },
    "iam.User": { collection: "iam.users", record: { name: "iam.users.record", param: "id" } },
  },
  auth: { user: { id: "usr_reviewer", name: "Reviewer" }, status: "authenticated" as const, hasRole: () => false },
};

function DecisionStory({ settled = false, inbox = false, conflict = false, invalidAttempt = false, readOnly = false, emptyFacts = false, pendingEmpty = false, siblingClosed = false }: {
  settled?: boolean; inbox?: boolean; conflict?: boolean; invalidAttempt?: boolean; readOnly?: boolean; emptyFacts?: boolean;
  pendingEmpty?: boolean; siblingClosed?: boolean;
}) {
  const schemas = useMemo(() => {
    let conflicting = conflict;
    let rejectAttempt = invalidAttempt;
    let current = decisionFixture({ can_act: !readOnly, context: { facts: [{ pointer: "/reference", label: "Reference", value: "R-7", authority: "source" }], references: [] } });
    if (settled) current = { ...current, is_open: false, can_act: false, verdict: "COMPLETED", closed_reason: "RESOLVED", resolution: { action: "accept", note: "Already reviewed", reference: "R-7" },
      resolved_by: { display_name: "Reviewer" }, resolved_at: "2026-09-29T09:30:00Z" };
    if (emptyFacts) current = { ...current, expires_at: null, resolved_by: null, resolved_at: null, closed_reason: null };
    if (pendingEmpty) current = { ...current, requester: null, expires_at: null };
    if (siblingClosed) current = { ...current, is_open: false, can_act: false, verdict: "PENDING", closed_reason: "SIBLING_SETTLED", resolution: {} };
    const fixture = storySchema(async (_input, init) => {
      const { query, variables } = v.parse(RequestSchema, JSON.parse(String(init?.body ?? "{}")));
      if (query.includes("decide(")) {
        if (conflicting) { current = { ...current, revision: current.revision + 1 }; conflicting = false; }
        if (variables.revision !== current.revision) return jsonResponse({ data: { decide: {
          ok: false, message: "The decision has changed.", validation_errors: { revision: ["Reload the decision."] },
        } } });
        if (rejectAttempt) {
          rejectAttempt = false;
          current = { ...current, revision: current.revision + 1 };
          return jsonResponse({ data: { decide: { ok: false, message: "Check the answer.", validation_errors: { note: ["Add the missing detail."] } } } });
        }
        const action = variables.action;
        if ((action !== "accept" && action !== "reject") || !isJsonObject(variables.values)) throw new Error("Unexpected story decision payload.");
        current = { ...current, is_open: false, can_act: false, revision: current.revision + 1, verdict: action === "reject" ? "REJECTED" : "COMPLETED", closed_reason: "RESOLVED",
          resolution: { action, ...variables.values }, resolved_by: { display_name: "Reviewer" }, resolved_at: "2026-09-29T09:30:00Z" };
        return jsonResponse({ data: { decide: { ok: true, message: "Decision recorded.", id: current.id } } });
      }
      if (query.includes("decisions_by_pk")) return jsonResponse({ data: { decisions_by_pk: { ...current, kind_label: "Review",
        assignees: [{ id: "usr_reviewer", display_name: "Reviewer" }] } } });
      if (query.includes("notes_by_pk")) return jsonResponse({ data: { notes_by_pk: { id: "nte_7", display_name: "Review notes" } } });
      return jsonResponse({ data: { decisions: [{ ...current, kind_label: "Review", assignees: [{ id: "usr_reviewer", display_name: "Reviewer" }] }], decisions_aggregate: { aggregate: { count: 1 } } } });
    }).public!;
    return { public: fixture, console: { ...fixture, metadata: { angee: { resources: [
      decisionResourceFixture,
      decisionGroupFixture,
      decisionSubjectFixture,
      decisionUserFixture,
    ] } } } };
  }, [settled, conflict, invalidAttempt, readOnly, emptyFacts, pendingEmpty, siblingClosed]);
  return <RoutedRuntimeFixture activeSchema="console" schemas={schemas} collectionPath="/decisions" initialEntry={inbox ? "/decisions" : "/decisions/dcn_review"}
    runtime={runtime} resourceName={DECISION_MODEL} resourceLabel="Decisions" operationDocuments={documents}>
    <InboxPage />
  </RoutedRuntimeFixture>;
}
