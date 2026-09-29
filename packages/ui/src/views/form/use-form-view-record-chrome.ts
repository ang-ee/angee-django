import * as React from "react";
import {
  rowPublicId,
  type DataResourceMetadata,
  type Row,
} from "@angee/metadata";

import {
  useModelSlot,
  type ModelSlotTarget,
  type SlotContribution,
} from "../../runtime";
import { optionToken } from "../../widgets/types";
import type { RecordChromeContext } from "../resource/record-chrome-context";
import {
  FORM_VIEW_RECORD_ACTIONS_SLOT,
  FORM_VIEW_SECTIONS_SLOT,
  formViewRecordActionsSlot,
} from "./form-view-slots";

export interface UseFormViewRecordChromeProps {
  /** Validated by the form surface against the complete section/verb inventory. */
  admitContributions?: readonly string[];
  dataResource: DataResourceMetadata | null;
  modelLabel: string;
  canonicalResource: string;
  id: string | null | undefined;
  isCreate: boolean;
  record: Row | null;
  formReadOnly: boolean;
  actionsBlocked?: boolean;
}

export interface FormViewRecordChromeSurface {
  recordChromeContext: RecordChromeContext | null;
  recordActions: readonly SlotContribution[];
}

/** Resolve passive chrome and increasingly-specific record-action slots. */
export function useFormViewRecordChrome({
  admitContributions,
  dataResource,
  modelLabel,
  canonicalResource,
  id,
  isCreate,
  record,
  formReadOnly,
  actionsBlocked = false,
}: UseFormViewRecordChromeProps): FormViewRecordChromeSurface {
  const recordChromeContext = React.useMemo<RecordChromeContext | null>(
    () =>
      isCreate || id == null || dataResource === null
        ? null
        : {
            resource: modelLabel,
            dataProviderName: dataResource.schemaName,
            canonicalResource,
            recordId: rowPublicId(record) ?? id,
            record,
            formReadOnly,
            actionsBlocked,
          },
    [actionsBlocked, canonicalResource, dataResource, formReadOnly, id, isCreate, modelLabel, record],
  );
  const recordActionTargets = React.useMemo<readonly ModelSlotTarget[]>(() => {
    const targets = [formViewRecordActionsSlot(canonicalResource)];
    if (canonicalResource !== modelLabel) {
      targets.push(formViewRecordActionsSlot(modelLabel));
    }
    for (const field of dataResource?.implFields ?? []) {
      const impl = optionToken(record?.[field]);
      if (impl) targets.push(formViewRecordActionsSlot(modelLabel, impl));
    }
    return targets;
  }, [canonicalResource, dataResource, modelLabel, record]);
  const recordActionEntries = useModelSlot(recordActionTargets, {
    admit: admitContributions,
    inventorySlots: [FORM_VIEW_SECTIONS_SLOT, FORM_VIEW_RECORD_ACTIONS_SLOT],
    owner: `FormView "${modelLabel}"`,
  });
  const recordActions = React.useMemo(() => {
    const byId = new Map<string, SlotContribution>();
    for (const entry of recordActionEntries) byId.set(entry.id, entry);
    return [...byId.values()].sort(
      (left, right) => (left.sequence ?? 0) - (right.sequence ?? 0),
    );
  }, [recordActionEntries]);

  return { recordChromeContext, recordActions };
}
