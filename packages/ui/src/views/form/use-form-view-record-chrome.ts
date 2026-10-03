import * as React from "react";
import {
  rowPublicId,
  type DataResourceMetadata,
  type Row,
} from "@angee/metadata";

import { useContainer, type ComposedContainerChild, modelChain } from "../../runtime";
import { optionToken } from "../../widgets/types";
import type { RecordChromeContext } from "../resource/record-chrome-context";

export interface UseFormViewRecordChromeProps {
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
  /** The record verbs for the toolbar (`#actions`) and the overflow menu (`#actions-menu`). */
  recordActions: {
    primary: readonly ComposedContainerChild[];
    menu: readonly ComposedContainerChild[];
  };
}

/** Resolve passive chrome and the record verbs, with this row's variants and permissions applied. */
export function useFormViewRecordChrome({
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
  const models = React.useMemo(
    () => modelChain(canonicalResource, modelLabel),
    [canonicalResource, modelLabel],
  );
  // The row's ImplClassField values select the bridges' variants of a verb.
  const impls = React.useMemo(
    () => (dataResource?.implFields ?? []).flatMap((field) => {
      const impl = optionToken(record?.[field]);
      return impl ? [impl] : [];
    }),
    [dataResource, record],
  );
  const primary = useContainer("form#actions", { models, row: record, impls });
  const menu = useContainer("form#actions-menu", { models, row: record, impls });
  const recordActions = React.useMemo(() => ({ primary, menu }), [menu, primary]);
  return { recordChromeContext, recordActions };
}
