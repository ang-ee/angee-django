import { refineResourceName, useModelMetadata, type Row } from "@angee/metadata";
import { extractActionOutcome, refineFieldsFromPaths } from "@angee/refine";
import { useActionResultRun, useAuthoredResourceMutation, optionToken } from "@angee/ui";
import { useCreate, useUpdate, type BaseRecord, type HttpError } from "@refinedev/core";

import type { ComparisonAnswer, ComparisonProposal } from "./comparison-data";
import { ANSWER_VISIBILITY } from "./documents";
import { ANSWER_MODEL, PROPOSAL_MODEL } from "./resources";

type RowRecord = BaseRecord & Row;

/** Write topic answers through the declared resource and its revision precondition. */
export function useComparisonAnswerWrite() {
  const resource = useModelMetadata(ANSWER_MODEL)?.resource ?? null;
  const target = refineResourceName(resource);
  const options = {
    resource: target,
    dataProviderName: resource?.schemaName,
    meta: { fields: refineFieldsFromPaths(["id", "body", "visibility", "revision"]) },
    invalidates: ["list", "many", "detail"] as ("list" | "many" | "detail")[],
    successNotification: false,
    errorNotification: false,
  };
  const create = useCreate<RowRecord, HttpError, Record<string, unknown>>(options);
  const update = useUpdate<RowRecord, HttpError, Record<string, unknown>>(options);
  const [setVisibility, visibilityState] = useAuthoredResourceMutation(ANSWER_VISIBILITY, { invalidateModels: [ANSWER_MODEL] });
  const settle = useActionResultRun();

  return {
    pending: create.mutation.isPending || update.mutation.isPending || visibilityState.fetching,
    save: async (proposalId: string, topicId: string, body: string, visibility: string, answer?: ComparisonAnswer) => {
      if (!resource) throw new Error("The answer resource is unavailable.");
      if (answer) {
        if (typeof answer.revision !== "number") throw new Error("The answer revision is unavailable.");
        let revision = answer.revision;
        if (body !== String(answer.body ?? "")) {
          const result = await update.mutateAsync({
            id: answer.id,
            values: { body },
            meta: { ...options.meta, gqlVariables: { expected_revision: revision } },
          });
          revision = typeof result.data.revision === "number" ? result.data.revision : revision;
        }
        if (optionToken(visibility) !== optionToken(answer.visibility)) {
          const outcome = await settle(async () => extractActionOutcome(await setVisibility({
            answer: answer.id, revision, visibility: visibility.toUpperCase() as "ROUND" | "RESPONDER" | "SEALED",
          }), "set_proposal_answer_visibility"));
          return outcome?.ok === true;
        }
      } else {
        await create.mutateAsync({ values: { proposal: proposalId, topic: topicId, body, visibility } });
      }
      return true;
    },
  };
}

/** The statement stays on its Proposal and uses the same resource update contract. */
export function useComparisonStatementWrite() {
  const resource = useModelMetadata(PROPOSAL_MODEL)?.resource ?? null;
  const update = useUpdate<RowRecord, HttpError, Record<string, unknown>>({
    resource: refineResourceName(resource),
    dataProviderName: resource?.schemaName,
    meta: { fields: refineFieldsFromPaths(["id", "statement", "revision"]) },
    invalidates: ["list", "many", "detail"],
    successNotification: false,
    errorNotification: false,
  });
  return {
    pending: update.mutation.isPending,
    save: async (proposal: ComparisonProposal, statement: string) => {
      if (!resource || typeof proposal.revision !== "number") throw new Error("The proposal revision is unavailable.");
      await update.mutateAsync({
        id: proposal.id,
        values: { statement },
        meta: {
          fields: refineFieldsFromPaths(["id", "statement", "revision"]),
          gqlVariables: { expected_revision: proposal.revision },
        },
      });
    },
  };
}
