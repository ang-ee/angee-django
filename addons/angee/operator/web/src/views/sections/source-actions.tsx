import { useMemo } from "react";

import {
  SOURCE_FETCH_MUTATION,
  SOURCE_PULL_MUTATION,
  SOURCE_PUSH_MUTATION,
} from "../../data/documents.daemon";
import { useOperatorT } from "../../i18n";
import { useOperatorAction } from "../../data/transport";
import type { SourceState } from "../../data/types";
import { useRunDaemonAction } from "../parts/run-action";
import type { RowAction } from "../parts/RowActions";

/** A git action for a source: its label, tone, and bound handler. */
export type SourceRowAction = RowAction<SourceState>;

/** Source git actions shared by the source detail page. */
export function useSourceActions(refetch: () => void): {
  actions: readonly SourceRowAction[];
  busy: boolean;
} {
  const t = useOperatorT();
  const runDaemon = useRunDaemonAction(refetch);

  const fetchSource = useOperatorAction(SOURCE_FETCH_MUTATION);
  const pull = useOperatorAction(SOURCE_PULL_MUTATION);
  const push = useOperatorAction(SOURCE_PUSH_MUTATION);
  const busy = fetchSource.result.fetching || pull.result.fetching || push.result.fetching;

  const actions = useMemo<readonly SourceRowAction[]>(() => {
    const defs: readonly {
      field: string;
      label: string;
      variant: SourceRowAction["variant"];
      run: (variables: { name: string }) => Promise<object | undefined>;
    }[] = [
      { field: "sourceFetch", label: t("operator.sources.fetch"), variant: "secondary", run: fetchSource.run },
      { field: "sourcePull", label: t("operator.sources.pull"), variant: "ghost", run: pull.run },
      { field: "sourcePush", label: t("operator.sources.push"), variant: "ghost", run: push.run },
    ];
    return defs.map((def) => ({
      label: def.label,
      variant: def.variant,
      perform: (source: SourceState) => {
        void runDaemon({
          run: def.run,
          field: def.field,
          variables: { name: source.name },
          label: def.label,
        });
      },
    }));
  }, [fetchSource.run, pull.run, push.run, runDaemon, t]);

  return { actions, busy };
}
