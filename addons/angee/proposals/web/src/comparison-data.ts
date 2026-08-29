import {
  refineResourceName,
  useModelMetadata,
  type DataResourceMetadata,
  type Row,
} from "@angee/metadata";
import { refineFieldsFromPaths } from "@angee/refine";
import {
  useList,
  useOne,
  type BaseRecord,
  type HttpError,
} from "@refinedev/core";
import * as React from "react";

import type { RoundActionRow } from "./round-actions";
import {
  ANSWER_MODEL,
  PROPOSAL_MODEL,
  ROUND_MODEL,
  TOPIC_MODEL,
} from "./resources";

type RowRecord = BaseRecord & Row;

export interface ComparisonRound extends RoundActionRow {
  name?: unknown;
}

export interface ComparisonTopic extends Row {
  id: string;
  key?: unknown;
  name?: unknown;
  hint?: unknown;
  sort_order?: unknown;
}

export interface ComparisonProposal extends Row {
  id: string;
  responder?: unknown;
  party?: { display_name?: unknown } | null;
  state?: unknown;
  cost?: unknown;
  currency?: { code?: unknown } | null;
  staffing?: unknown;
  timeframe_start?: unknown;
  timeframe_end?: unknown;
  confidence?: unknown;
  valid_until?: unknown;
}

export interface ComparisonAnswer extends Row {
  id: string;
  proposal?: { id?: string } | null;
  topic?: { id?: string } | null;
  body?: unknown;
}

export interface RoundComparisonState {
  round: ComparisonRound | null;
  topics: readonly ComparisonTopic[];
  proposals: readonly ComparisonProposal[];
  answers: readonly ComparisonAnswer[];
  fetching: boolean;
  error: HttpError | null;
}

/** Refine/metadata-owned reads for one bounded comparison matrix. */
export function useRoundComparisonData(roundId: string): RoundComparisonState {
  const roundMetadata = useModelMetadata(ROUND_MODEL);
  const topicMetadata = useModelMetadata(TOPIC_MODEL);
  const proposalMetadata = useModelMetadata(PROPOSAL_MODEL);
  const answerMetadata = useModelMetadata(ANSWER_MODEL);
  const roundResource = roundMetadata?.resource ?? null;
  const topicResource = topicMetadata?.resource ?? null;
  const proposalResource = proposalMetadata?.resource ?? null;
  const answerResource = answerMetadata?.resource ?? null;

  const roundFields = React.useMemo(
    () => refineFieldsFromPaths(["id", "name", "status", "opening_policy"]),
    [],
  );
  const topicFields = React.useMemo(
    () => refineFieldsFromPaths(["id", "key", "name", "hint", "sort_order"]),
    [],
  );
  const proposalFields = React.useMemo(
    () =>
      refineFieldsFromPaths([
        "id",
        "responder.id",
        "responder.display_name",
        "party.display_name",
        "state",
        "cost",
        "currency.code",
        "staffing",
        "timeframe_start",
        "timeframe_end",
        "confidence",
        "valid_until",
      ]),
    [],
  );
  const answerFields = React.useMemo(
    () => refineFieldsFromPaths(["id", "proposal.id", "topic.id", "body"]),
    [],
  );

  const roundRun = useOne<RowRecord, HttpError>({
    resource: resourceName(roundResource),
    id: roundId,
    dataProviderName: roundResource?.schemaName,
    meta: { fields: roundFields },
    queryOptions: { enabled: Boolean(roundId) && roundResource !== null },
  });
  const topicRun = useList<RowRecord, HttpError>({
    resource: resourceName(topicResource),
    dataProviderName: topicResource?.schemaName,
    pagination: { mode: "off" },
    filters: [{ field: "round", operator: "eq", value: roundId }],
    sorters: [
      { field: "sort_order", order: "asc" },
      { field: "key", order: "asc" },
    ],
    meta: { fields: topicFields },
    queryOptions: { enabled: Boolean(roundId) && topicResource !== null },
  });
  const proposalRun = useList<RowRecord, HttpError>({
    resource: resourceName(proposalResource),
    dataProviderName: proposalResource?.schemaName,
    pagination: { mode: "off" },
    filters: [{ field: "round", operator: "eq", value: roundId }],
    sorters: [
      { field: "state", order: "asc" },
      { field: "created_at", order: "asc" },
    ],
    meta: { fields: proposalFields },
    queryOptions: { enabled: Boolean(roundId) && proposalResource !== null },
  });
  const proposals = React.useMemo(
    () => (proposalRun.result.data ?? []) as ComparisonProposal[],
    [proposalRun.result.data],
  );
  const proposalIds = React.useMemo(
    () => proposals.map((proposal) => proposal.id),
    [proposals],
  );
  const answerRun = useList<RowRecord, HttpError>({
    resource: resourceName(answerResource),
    dataProviderName: answerResource?.schemaName,
    pagination: { mode: "off" },
    filters: [{ field: "proposal", operator: "in", value: proposalIds }],
    meta: { fields: answerFields },
    queryOptions: {
      enabled: proposalIds.length > 0 && answerResource !== null,
    },
  });

  return {
    round: (roundRun.result as ComparisonRound | undefined) ?? null,
    topics: (topicRun.result.data ?? []) as ComparisonTopic[],
    proposals,
    answers: (answerRun.result.data ?? []) as ComparisonAnswer[],
    fetching:
      roundRun.query.isFetching
      || topicRun.query.isFetching
      || proposalRun.query.isFetching
      || answerRun.query.isFetching,
    error:
      roundRun.query.error
      ?? topicRun.query.error
      ?? proposalRun.query.error
      ?? answerRun.query.error
      ?? null,
  };
}

function resourceName(resource: DataResourceMetadata | null): string {
  return resource ? refineResourceName(resource) : "__angee_disabled__";
}
