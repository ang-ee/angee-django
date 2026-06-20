import { useToast } from "@angee/base";
import { useMemo } from "react";

import {
  SOURCE_FETCH_MUTATION,
  SOURCE_PULL_MUTATION,
  SOURCE_PUSH_MUTATION,
} from "../../data/documents.daemon";
import { useOperatorT } from "../../i18n";
import { useOperatorAction } from "../../data/transport";
import type { SourceState } from "../../data/types";
import { runDaemonAction } from "../parts/run-action";
import type { RowAction } from "../parts/RowActions";

/** A git action for a source: its label, tone, and bound handler. */
export type SourceRowAction = RowAction<SourceState>;

/**
 * The three source git actions, each run via {@link runDaemonAction} and
 * surfacing a failure as a toast — the live snapshot then reflects the new state,
 * so callers need no local result store. Sources have no destructive action, so
 * none confirms. Shared by the source detail page.
 */
export function useSourceActions(refetch: () => void): {
  actions: readonly SourceRowAction[];
  busy: boolean;
} {
  const t = useOperatorT();
  const toast = useToast();

  const fetchSource = useOperatorAction(SOURCE_FETCH_MUTATION);
  const pull = useOperatorAction(SOURCE_PULL_MUTATION);
  const push = useOperatorAction(SOURCE_PUSH_MUTATION);
  const busy = fetchSource.result.fetching || pull.result.fetching || push.result.fetching;

  const actions = useMemo<readonly SourceRowAction[]>(() => {
    const defs: readonly {
      field: string;
      label: string;
      variant: SourceRowAction["variant"];
      run: (variables: { name: string }) => Promise<object>;
    }[] = [
      { field: "sourceFetch", label: t("operator.sources.fetch"), variant: "secondary", run: fetchSource.run },
      { field: "sourcePull", label: t("operator.sources.pull"), variant: "ghost", run: pull.run },
      { field: "sourcePush", label: t("operator.sources.push"), variant: "ghost", run: push.run },
    ];
    return defs.map((def) => ({
      label: def.label,
      variant: def.variant,
      perform: (source: SourceState) => {
        void runDaemonAction({
          run: def.run,
          field: def.field,
          variables: { name: source.name },
          label: def.label,
          toast,
          refetch,
        });
      },
    }));
  }, [fetchSource.run, pull.run, push.run, refetch, t, toast]);

  return { actions, busy };
}
