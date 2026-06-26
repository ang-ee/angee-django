import * as React from "react";

import { cn } from "../lib/cn";
import {
  Page,
  PageAside,
  PageBody,
  PageToolbar,
  type PageAsideProps,
  type PageToolbarProps,
} from "../page";
import {
  createLayoutSlot,
  findLayoutSlot,
  withoutLayoutSlots,
  type LayoutSlotComponent,
} from "./slots";

const TOOLBAR_SLOT = Symbol.for("@angee/base.canvas-toolbar-slot");
const ASIDE_SLOT = Symbol.for("@angee/base.canvas-aside-slot");

export interface CanvasPageProps {
  asideSide?: "left" | "right";
  chrome?: boolean;
  className?: string;
  children?: React.ReactNode;
}

export type CanvasPageToolbarProps = PageToolbarProps;
export type CanvasPageAsideProps = PageAsideProps;

const CanvasPageToolbar = createLayoutSlot<CanvasPageToolbarProps>(
  TOOLBAR_SLOT,
  "CanvasPage.Toolbar",
);
const CanvasPageAside = createLayoutSlot<CanvasPageAsideProps>(
  ASIDE_SLOT,
  "CanvasPage.Aside",
);

type CanvasPageComponent = ((props: CanvasPageProps) => React.ReactElement) & {
  Toolbar: LayoutSlotComponent<CanvasPageToolbarProps>;
  Aside: LayoutSlotComponent<CanvasPageAsideProps>;
};

export const CanvasPage = Object.assign(
  function CanvasPage({
    asideSide = "right",
    chrome = true,
    className,
    children,
  }: CanvasPageProps): React.ReactElement {
    const toolbar = findLayoutSlot<CanvasPageToolbarProps>(
      children,
      TOOLBAR_SLOT,
    );
    const aside = findLayoutSlot<CanvasPageAsideProps>(children, ASIDE_SLOT);
    const canvas = withoutLayoutSlots(children, [TOOLBAR_SLOT, ASIDE_SLOT]);

    const asidePanel = aside ? (
      <PageAside
        {...aside.props}
        className={cn(
          asideSide === "left" ? "border-l-0 border-r" : undefined,
          aside.props.className,
        )}
      />
    ) : null;

    const frame = (
      <div
        className={cn(
          "flex min-h-0 flex-1 flex-col",
          chrome ? undefined : className,
        )}
      >
        {toolbar ? <PageToolbar {...toolbar.props} /> : null}
        <div className="flex min-h-0 flex-1">
          {asideSide === "left" ? asidePanel : null}
          <div className="relative min-h-0 min-w-0 flex-1 overflow-hidden">
            {canvas}
          </div>
          {asideSide === "right" ? asidePanel : null}
        </div>
      </div>
    );

    if (!chrome) return frame;

    return (
      <Page className={cn("min-h-full", className)}>
        <PageBody className="flex flex-col" gutter="none" scroll="hidden">
          {frame}
        </PageBody>
      </Page>
    );
  },
  {
    Toolbar: CanvasPageToolbar,
    Aside: CanvasPageAside,
  },
) satisfies CanvasPageComponent;
