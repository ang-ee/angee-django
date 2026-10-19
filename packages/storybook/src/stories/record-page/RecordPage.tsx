import * as React from "react";
import { ContextAside, ToolsEdge } from "./ContextAside";
import { ExplorerPane } from "./ExplorerPane";
import { Subject } from "./Subject";
import type { AsideData, AsideTab, ExplorerData, SubjectData } from "./types";

/**
 * The record page: explorer · subject · context aside · tools edge. Every record page in Angee
 * is this composition with its own data; nothing else is allowed to add a region.
 */
export interface RecordPageProps {
  explorer?: ExplorerData;
  subject: SubjectData;
  aside: AsideData;
  tab?: AsideTab;
  drillIn?: "run";
  menuOpen?: boolean;
}

export function RecordPage({ explorer, subject, aside, tab, drillIn, menuOpen }: RecordPageProps): React.ReactElement {
  return (
    <div className="flex h-[62rem] w-full overflow-hidden bg-canvas text-fg">
      {explorer ? <ExplorerPane data={explorer} /> : null}
      <Subject data={subject} menuOpen={menuOpen} />
      <ContextAside data={aside} tab={tab} drillIn={drillIn} />
      <ToolsEdge />
    </div>
  );
}
