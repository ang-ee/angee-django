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
  formViewRecordActionsSlot,
} from "./form-view-slots";

export interface UseFormViewRecordChromeProps {
  dataResource: DataResourceMetadata | null;
  modelLabel: string;
  canonicalResource: string;
  id: string | null | undefined;
  isCreate: boolean;
  record: Row | null;
}

export interface FormViewRecordChromeSurface {
  recordChromeContext: RecordChromeContext | null;
  recordActions: readonly SlotContribution[];
}

/** Resolve passive chrome and increasingly-specific record-action slots. */
export function useFormViewRecordChrome({
  dataResource,
  modelLabel,
  canonicalResource,
  id,
  isCreate,
  record,
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
          },
    [canonicalResource, dataResource, id, isCreate, modelLabel, record],
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
  const recordActionEntries = useModelSlot(recordActionTargets);
  const recordActions = React.useMemo(() => {
    const byId = new Map<string, SlotContribution>();
    for (const entry of recordActionEntries) byId.set(entry.id, entry);
    return [...byId.values()].sort(
      (left, right) => (left.sequence ?? 0) - (right.sequence ?? 0),
    );
  }, [recordActionEntries]);

  return { recordChromeContext, recordActions };
}
