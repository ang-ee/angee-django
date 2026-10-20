import { useState } from "react";
import {
  AppRuntimeProvider, ChatterProvider, Drawer, EmptyState, PaneLabel, PaneRow,
  PaneRows, PrimaryPaneProvider, TooltipProvider, cn, defaultWidgets,
  textRoleVariants, useChatter,
} from "@angee/ui";
import { ContextAside } from "./ContextAside";
import { Navigator, TreeNavigator } from "./Navigator";
import { Subject } from "./Subject";
import { IconControl, Spec } from "./spec";
import type { AsideTab, PageKind } from "./fixtures";
import "./record-page-target.css";

export interface RecordPageTargetProps {
  page: PageKind;
  aside?: AsideTab;
  runOpen?: boolean;
  messageOpen?: boolean;
  anatomy?: boolean;
}

/** Dated drawing, never an exported framework owner. Delete per page on landing. */
export function RecordPageTarget({ page, aside = "timeline", runOpen = false, ...props }: RecordPageTargetProps) {
  return <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
    <TooltipProvider><PrimaryPaneProvider>
      <ChatterProvider key={`${page}:${aside}:${runOpen}:${props.messageOpen}`} defaultCollapsed={false} defaultTab={runOpen ? "run" : aside}>
        <TargetContent page={page} {...props} />
      </ChatterProvider>
    </PrimaryPaneProvider></TooltipProvider>
  </AppRuntimeProvider>;
}

function TargetContent({ page, messageOpen = false, anatomy = false }: RecordPageTargetProps) {
  const [readingMessage, setReadingMessage] = useState(messageOpen);
  const { setActiveTab } = useChatter();
  return <div className={cn("record-target", anatomy && "target-anatomy")} data-target-page={page}>
    {anatomy ? <div className="target-anatomy-legend">
      <h1 className={textRoleVariants({ role: "title" })}>2026-10-20 · Record page target</h1>
      <p className={textRoleVariants({ role: "caption" })}>Solid outlines: the four regions. Dashed outlines with “spec”: anatomy awaiting its owner.</p>
      <div className={cn("target-anatomy-columns", textRoleVariants({ role: "description" }))}>
        <p>Navigator<br /><span>ScopedExplorerPane · region<br />TreeView / list / lens · surface</span></p>
        <p>Subject<br /><span>RecordPage · page (spec)<br />RecordHeader + CommandBand · regions (spec)<br />Field / Group + EditableLines · contributions / surface</span></p>
        <p>Context aside<br /><span>Chatter · surface<br />PaneToolbar · region · PaneRow · fragment<br />RelatedPane / Decisions · surface / contribution</span></p>
        <p className="target-tools-legend">Tools edge<br /><span>DrawerRail · region (spec)</span></p>
      </div>
    </div> : null}
    <div className="target-four-regions">
      <Navigator page={page} />
      {page === "file" || page === "knowledge" ? <TreeNavigator page={page} /> : null}
      <Subject page={page} messageOpen={readingMessage} onMessage={setReadingMessage} onAside={setActiveTab} />
      <ContextAside page={page} />
      <ToolsEdge />
    </div>
  </div>;
}

function ToolsEdge() {
  return <Spec owner="DrawerRail · compact edge" className="target-tools">
    <div data-region="tools-edge" role="toolbar" aria-label="Tools edge" className="flex flex-col items-center gap-2 py-2">
      {(["Agents", "Logs"] as const).map((tool) => <Drawer.Root key={tool}>
        <Drawer.Trigger render={<IconControl icon={tool === "Agents" ? "agent" : "terminal"} label={tool} />} />
        <Drawer.Portal><Drawer.Backdrop /><Drawer.Content side="right">
          <Drawer.Header><Drawer.Title>{tool}</Drawer.Title><Drawer.Description>Session tools</Drawer.Description><Drawer.Close /></Drawer.Header>
          <Drawer.Body>{tool === "Agents" ? <EmptyState icon="agent" title="Bill import assistant" description="Run and node inspection belongs in this tools drawer." />
            : <PaneRows><PaneLabel>Oct 20, 2026</PaneLabel><li><PaneRow icon="terminal" title="Bill import is waiting" sub="Review contract deviations" meta="09:41 AM" /></li></PaneRows>}</Drawer.Body>
        </Drawer.Content></Drawer.Portal>
      </Drawer.Root>)}
    </div>
  </Spec>;
}
