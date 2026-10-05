import { useMemo } from "react";
import * as v from "valibot";
import { operationDocuments } from "@angee/gql/console/actions";
import { RoutedRuntimeFixture, jsonResponse, storySchema } from "@angee/storybook/testing";
import { createRouteHref, JsonValueSchema } from "@angee/ui";

import { ProposalSchema } from "./proposal";
import { InboxPage } from "./InboxPage";
import { DECISION_MODEL } from "./documents.console";
import { decisionFixture, decisionLinkFixture, decisionResourceFixture, decisionRecordFixture, decisionUserFixture } from "./testing";

export default { title: "Decisions/Inbox", parameters: { layout: "fullscreen" } };
export const Open = { render: () => <DecisionStory /> };
export const Closed = { render: () => <DecisionStory closed /> };
export const ClosedWithoutAttribution = { render: () => <DecisionStory closed emptyFacts /> };
export const OpenWithoutRequester = { render: () => <DecisionStory pendingEmpty /> };
export const Multiple = { render: () => <DecisionStory multiple /> };
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

function DecisionStory({ multiple = false, closed = false, inbox = false, conflict = false, invalidAttempt = false, readOnly = false, emptyFacts = false, pendingEmpty = false }: {
  multiple?: boolean; closed?: boolean; inbox?: boolean; conflict?: boolean; invalidAttempt?: boolean; readOnly?: boolean; emptyFacts?: boolean;
  pendingEmpty?: boolean;
}) {
  const schemas = useMemo(() => {
    let conflicting = conflict;
    let rejectAttempt = invalidAttempt;
    let current = decisionFixture({ permissions: readOnly ? [] : ["act"], context: { facts: [{ pointer: "/reference", label: "Reference", value: "R-7", authority: "source" }], references: [] } });
    if (multiple) current = { ...current, proposal: { ...v.parse(ProposalSchema, current.proposal), multiple: true } };
    if (closed) current = { ...current, is_open: false, permissions: [], verdict: ["accept"], verdict_label: "Accept and archive",
      answered_by: { display_name: "Reviewer" }, answered_at: "2026-09-29T09:30:00Z" };
    if (emptyFacts) current = { ...current, answered_by: null, answered_at: null };
    if (pendingEmpty) current = { ...current, requester: null };

    const fixture = storySchema(async (_input, init) => {
      const { query, variables } = v.parse(RequestSchema, JSON.parse(String(init?.body ?? "{}")));
      if (query.includes("decide(")) {
        if (conflicting) { current = { ...current, revision: current.revision + 1 }; conflicting = false; }
        if (variables.revision !== current.revision) return jsonResponse({ data: null, errors: [{
          message: "The decision has changed.", path: ["decide"], extensions: { code: "STALE_REVISION" },
        }] });
        if (rejectAttempt) {
          rejectAttempt = false;
          return jsonResponse({ data: { decide: { ok: false, message: "Check the answer.", validation_errors: { chosen: ["Choose an offered alternative."] } } } });
        }
        const chosen = v.parse(v.array(v.string()), variables.chosen);
        if (!chosen.length || chosen.some((key) => key !== "accept" && key !== "reject")) throw new Error("Unexpected choice.");
        current = { ...current, is_open: false, permissions: [], revision: current.revision + 1,
          verdict: chosen, verdict_label: chosen.map((key) => key === "accept" ? "Accept and archive" : "Keep what is on the record").join("; "),
          answered_by: { display_name: "Reviewer" }, answered_at: "2026-09-29T09:30:00Z" };
        return jsonResponse({ data: { decide: { ok: true, message: "Decision recorded.", id: current.id } } });
      }
      if (query.includes("decisions_by_pk")) return jsonResponse({ data: { decisions_by_pk: { ...current, kind_label: "Review",
        assignees: [{ id: "usr_reviewer", display_name: "Reviewer" }] } } });
      if (query.includes("notes_by_pk")) return jsonResponse({ data: { notes_by_pk: { id: "nte_7", display_name: "Review notes" } } });
      return jsonResponse({ data: { decisions: [{ ...current, kind_label: "Review", assignees: [{ id: "usr_reviewer", display_name: "Reviewer" }] }], decisions_aggregate: { aggregate: { count: 1 } } } });
    }).public!;
    return { public: fixture, console: { ...fixture, metadata: { angee: { resources: [
      decisionResourceFixture,
      decisionRecordFixture,
      decisionLinkFixture,
      decisionUserFixture,
    ] } } } };
  }, [multiple, closed, conflict, invalidAttempt, readOnly, emptyFacts, pendingEmpty ]);
  return <RoutedRuntimeFixture activeSchema="console" schemas={schemas} collectionPath="/decisions" initialEntry={inbox ? "/decisions" : "/decisions/dcn_review"}
    runtime={runtime} resourceName={DECISION_MODEL} resourceLabel="Decisions" operationDocuments={documents}>
    <InboxPage />
  </RoutedRuntimeFixture>;
}
