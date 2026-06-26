import * as React from "react";

import { cn } from "../lib/cn";
import {
  Page,
  PageBody,
  SplitPane,
  SplitPaneHandle,
  SplitPanes,
} from "../page";
import {
  createLayoutSlot,
  findLayoutSlot,
  type LayoutSlotComponent,
} from "./slots";

const PRIMARY_SLOT = Symbol.for("@angee/base.split-primary-slot");
const DETAIL_SLOT = Symbol.for("@angee/base.split-detail-slot");

export interface SplitViewProps {
  autoSave?: string;
  chrome?: boolean;
  className?: string;
  children?: React.ReactNode;
  direction?: "horizontal" | "vertical";
  primarySize?: number;
}

interface SplitViewSlotProps {
  children?: React.ReactNode;
}

const SplitViewPrimary = createLayoutSlot<SplitViewSlotProps>(
  PRIMARY_SLOT,
  "SplitView.Primary",
);
const SplitViewDetail = createLayoutSlot<SplitViewSlotProps>(
  DETAIL_SLOT,
  "SplitView.Detail",
);

type SplitViewComponent = ((props: SplitViewProps) => React.ReactElement) & {
  Primary: LayoutSlotComponent<SplitViewSlotProps>;
  Detail: LayoutSlotComponent<SplitViewSlotProps>;
};

export const SplitView = Object.assign(
  function SplitView({
    autoSave,
    chrome = true,
    className,
    children,
    direction = "horizontal",
    primarySize = 32,
  }: SplitViewProps): React.ReactElement {
    const primary = findLayoutSlot<SplitViewSlotProps>(children, PRIMARY_SLOT);
    const detail = findLayoutSlot<SplitViewSlotProps>(children, DETAIL_SLOT);

    const split = (
      <SplitPanes
        autoSave={autoSave}
        className={chrome ? undefined : className}
        direction={direction}
      >
        <SplitPane defaultSize={primarySize} minSize={15}>
          {primary?.props.children}
        </SplitPane>
        <SplitPaneHandle direction={direction} />
        <SplitPane minSize={20}>{detail?.props.children}</SplitPane>
      </SplitPanes>
    );

    if (!chrome) return split;

    return (
      <Page className={cn("min-h-full", className)}>
        <PageBody gutter="none" scroll="hidden">
          {split}
        </PageBody>
      </Page>
    );
  },
  {
    Primary: SplitViewPrimary,
    Detail: SplitViewDetail,
  },
) satisfies SplitViewComponent;
