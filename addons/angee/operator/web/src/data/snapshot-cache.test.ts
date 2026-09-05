import { authoredQueryKey } from "@angee/refine";
import { QueryClient } from "@tanstack/react-query";
import { expect, test } from "vitest";
import { SNAPSHOT_QUERY } from "./documents.daemon";
import { OPERATOR_PROVIDER } from "./operator-provider";
import { updateSnapshotQueries } from "./snapshot-cache";
import type { OperatorSnapshotQueryData, OperatorSnapshotQueryVariables } from "./types";

const none: OperatorSnapshotQueryVariables = { wantOverview: false, wantServices: false, wantWorkspaces: false, wantSources: false, wantGitOps: false, wantOperations: false, wantTemplates: false, wantSecrets: false };
const service = { id: "worker", name: "worker", runtime: "process", status: "running", health: null };
const servicesKey = authoredQueryKey(SNAPSHOT_QUERY, { ...none, wantServices: true }, OPERATOR_PROVIDER);
const sourcesKey = authoredQueryKey(SNAPSHOT_QUERY, { ...none, wantSources: true }, OPERATOR_PROVIDER);

test("pushes update only requested fields and preserve sections absent from a partial push", () => {
  const client = new QueryClient();
  client.setQueryData(servicesKey, { services: [] });
  client.setQueryData(sourcesKey, { sources: [] });
  updateSnapshotQueries(client, { services: [service] });
  expect(client.getQueryData(servicesKey)).toEqual({ services: [service] });
  expect(client.getQueryData(sourcesKey)).toEqual({ sources: [] });
  updateSnapshotQueries(client, { services: [], sources: [] });
  expect(client.getQueryData(servicesKey)).toEqual({ services: [] });
  expect(client.getQueryData(sourcesKey)).toEqual({ sources: [] });
  client.clear();
});

test("native HTTP completion and post-mutation refetch can supersede live cache writes", async () => {
  const client = new QueryClient();
  let complete!: (data: OperatorSnapshotQueryData) => void;
  const request = client.fetchQuery({ queryKey: servicesKey, queryFn: () => new Promise<OperatorSnapshotQueryData>((resolve) => { complete = resolve; }) });
  updateSnapshotQueries(client, { services: [] });
  expect(client.getQueryData(servicesKey)).toEqual({ services: [] });
  complete({ services: [service] });
  await request;
  expect(client.getQueryData(servicesKey)).toEqual({ services: [service] });
  updateSnapshotQueries(client, { services: [] });
  await client.fetchQuery({ queryKey: servicesKey, staleTime: 0, queryFn: async () => ({ services: [service] }) });
  expect(client.getQueryData(servicesKey)).toEqual({ services: [service] });
  client.clear();
});

test("a provider-isolated query is not overwritten by daemon snapshots", () => {
  const client = new QueryClient();
  const other = authoredQueryKey(SNAPSHOT_QUERY, { ...none, wantServices: true }, "other");
  client.setQueryData(other, { services: [] });
  updateSnapshotQueries(client, { services: [service] });
  expect(client.getQueryData(other)).toEqual({ services: [] });
  client.clear();
});


test("an unrelated partial push cannot mark a pending pane as loaded", async () => {
  const client = new QueryClient();
  let complete!: (data: OperatorSnapshotQueryData) => void;
  const request = client.fetchQuery({ queryKey: sourcesKey, queryFn: () => new Promise<OperatorSnapshotQueryData>((resolve) => { complete = resolve; }) });
  updateSnapshotQueries(client, { services: [] });
  expect(client.getQueryData(sourcesKey)).toBeUndefined();
  complete({ sources: [] });
  await request;
  expect(client.getQueryData(sourcesKey)).toEqual({ sources: [] });
  client.clear();
});
