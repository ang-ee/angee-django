import { useMemo } from "react";

import {
  SOURCE_FETCH_MUTATION,
  SOURCE_PULL_MUTATION,
  SOURCE_PUSH_MUTATION,
} from "../../data/documents.daemon";
import { useOperatorT } from "../../i18n";
import { useOperatorAction } from "../../data/transport";
import type { SourceState } from "../../data/types";
import { useDaemonMutationRun, type DaemonVerb } from "../parts/daemon-verbs";

/** Source git verbs shared by the source record page. */
export function useSourceActions(refetch: () => void): {
  actions: readonly DaemonVerb<SourceState>[];
  busy: boolean;
} {
  const t = useOperatorT();
  const runDaemon = useDaemonMutationRun(refetch);

  const fetchSource = useOperatorAction(SOURCE_FETCH_MUTATION);
  const pull = useOperatorAction(SOURCE_PULL_MUTATION);
  const push = useOperatorAction(SOURCE_PUSH_MUTATION);
  const busy = fetchSource.result.fetching || pull.result.fetching || push.result.fetching;

  const actions = useMemo<readonly DaemonVerb<SourceState>[]>(() => [
    { id: "fetch", label: t("sources.fetch"), primary: true,
      perform: (source) => runDaemon({ run: fetchSource.run, field: "sourceFetch", variables: { name: source.name } }) },
    { id: "pull", label: t("sources.pull"),
      perform: (source) => runDaemon({ run: pull.run, field: "sourcePull", variables: { name: source.name } }) },
    { id: "push", label: t("sources.push"), placement: "menu",
      perform: (source) => runDaemon({ run: push.run, field: "sourcePush", variables: { name: source.name } }) },
  ], [fetchSource.run, pull.run, push.run, runDaemon, t]);

  return { actions, busy };
}
