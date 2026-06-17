import { Button, useToast } from "@angee/base";
import { useMemo, type ReactNode } from "react";

import {
  SOURCE_FETCH_MUTATION,
  SOURCE_PULL_MUTATION,
  SOURCE_PUSH_MUTATION,
} from "../../data/documents";
import { useOperatorT } from "../../i18n";
import { useOperatorAction } from "../../data/transport";
import type { SourceState } from "../../data/types";
import { runDaemonAction, type DaemonActionData } from "../parts/run-action";

interface SourceActionVars extends Record<string, unknown> {
  name: string;
}

/** A git action for a source: its label, tone, and bound handler. */
export interface SourceRowAction {
  label: string;
  variant: "secondary" | "ghost";
  perform: (source: SourceState) => void;
}

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

  const fetchSource = useOperatorAction<DaemonActionData, SourceActionVars>(SOURCE_FETCH_MUTATION);
  const pull = useOperatorAction<DaemonActionData, SourceActionVars>(SOURCE_PULL_MUTATION);
  const push = useOperatorAction<DaemonActionData, SourceActionVars>(SOURCE_PUSH_MUTATION);
  const busy = fetchSource.result.fetching || pull.result.fetching || push.result.fetching;

  const actions = useMemo<readonly SourceRowAction[]>(() => {
    const defs = [
      { field: "sourceFetch", label: t("operator.sources.fetch"), variant: "secondary" as const, run: fetchSource.run },
      { field: "sourcePull", label: t("operator.sources.pull"), variant: "ghost" as const, run: pull.run },
      { field: "sourcePush", label: t("operator.sources.push"), variant: "ghost" as const, run: push.run },
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
          setError: (message) => {
            if (message) toast.danger({ title: message });
          },
          refetch,
        });
      },
    }));
  }, [fetchSource.run, pull.run, push.run, refetch, t, toast]);

  return { actions, busy };
}

/** A horizontal bar of a source's git action buttons. */
export function SourceActions({
  actions,
  busy,
  source,
  className = "flex justify-end gap-1",
}: {
  actions: readonly SourceRowAction[];
  busy: boolean;
  source: SourceState;
  className?: string;
}): ReactNode {
  return (
    <div className={className}>
      {actions.map((action) => (
        <Button
          key={action.label}
          disabled={busy}
          onClick={() => action.perform(source)}
          size="sm"
          variant={action.variant}
        >
          {action.label}
        </Button>
      ))}
    </div>
  );
}
