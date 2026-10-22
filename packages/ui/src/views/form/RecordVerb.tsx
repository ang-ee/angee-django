import * as React from "react";

import { useRecordChromeContextMaybe } from "../resource/record-chrome-context";
import type { ActionDescriptor } from "../page/Action";
import { RecordActionBar } from "./RecordActionBar";

/**
 * One contributed record verb: a container child (`<model>#actions`,
 * `<model>#actions-menu`) renders its descriptor against the open record the
 * chrome context carries, and nothing while no record is open. Addons declare the
 * descriptor; this owns reading the record and composing `RecordActionBar`.
 * @kind fragment
 * @owner RecordActionBar
 */
export function RecordVerb({ action }: { action: ActionDescriptor }): React.ReactElement | null {
  const record = useRecordChromeContextMaybe()?.record;
  return record ? <RecordActionBar record={record} actions={[action]} /> : null;
}
