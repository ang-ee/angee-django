import * as React from "react";

import { cn } from "../lib/cn";
import {
  Page,
  PageAside,
  PageBody,
  PageFooter,
  PageHeader,
  PageToolbar,
  type PageAsideProps,
  type PageBodyProps,
  type PageFooterProps,
  type PageHeaderProps,
  type PageToolbarProps,
} from "../page";
import {
  createLayoutSlot,
  findLayoutSlot,
  withoutLayoutSlots,
  type LayoutSlotComponent,
} from "./slots";

const HEADER_SLOT = Symbol.for("@angee/base.record-header-slot");
const TOOLBAR_SLOT = Symbol.for("@angee/base.record-toolbar-slot");
const BODY_SLOT = Symbol.for("@angee/base.record-body-slot");
const ASIDE_SLOT = Symbol.for("@angee/base.record-aside-slot");
const FOOTER_SLOT = Symbol.for("@angee/base.record-footer-slot");

export interface RecordViewProps {
  asideSide?: "left" | "right";
  chrome?: boolean;
  className?: string;
  children?: React.ReactNode;
}

export type RecordViewHeaderProps = PageHeaderProps;
export type RecordViewToolbarProps = PageToolbarProps;
export type RecordViewBodyProps = PageBodyProps;
export type RecordViewAsideProps = PageAsideProps;
export type RecordViewFooterProps = PageFooterProps;

const RecordViewHeader = createLayoutSlot<RecordViewHeaderProps>(
  HEADER_SLOT,
  "RecordView.Header",
);
const RecordViewToolbar = createLayoutSlot<RecordViewToolbarProps>(
  TOOLBAR_SLOT,
  "RecordView.Toolbar",
);
const RecordViewBody = createLayoutSlot<RecordViewBodyProps>(
  BODY_SLOT,
  "RecordView.Body",
);
const RecordViewAside = createLayoutSlot<RecordViewAsideProps>(
  ASIDE_SLOT,
  "RecordView.Aside",
);
const RecordViewFooter = createLayoutSlot<RecordViewFooterProps>(
  FOOTER_SLOT,
  "RecordView.Footer",
);

type RecordViewComponent = ((props: RecordViewProps) => React.ReactElement) & {
  Header: LayoutSlotComponent<RecordViewHeaderProps>;
  Toolbar: LayoutSlotComponent<RecordViewToolbarProps>;
  Body: LayoutSlotComponent<RecordViewBodyProps>;
  Aside: LayoutSlotComponent<RecordViewAsideProps>;
  Footer: LayoutSlotComponent<RecordViewFooterProps>;
};

export const RecordView = Object.assign(
  function RecordView({
    asideSide = "right",
    chrome = true,
    className,
    children,
  }: RecordViewProps): React.ReactElement {
    const header = findLayoutSlot<RecordViewHeaderProps>(children, HEADER_SLOT);
    const toolbar = findLayoutSlot<RecordViewToolbarProps>(
      children,
      TOOLBAR_SLOT,
    );
    const body = findLayoutSlot<RecordViewBodyProps>(children, BODY_SLOT);
    const aside = findLayoutSlot<RecordViewAsideProps>(children, ASIDE_SLOT);
    const footer = findLayoutSlot<RecordViewFooterProps>(children, FOOTER_SLOT);
    const looseBody = withoutLayoutSlots(children, [
      HEADER_SLOT,
      TOOLBAR_SLOT,
      BODY_SLOT,
      ASIDE_SLOT,
      FOOTER_SLOT,
    ]);

    const bodyProps = body?.props;
    const bodyChildren = bodyProps ? bodyProps.children : looseBody;
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
        {header ? <PageHeader {...header.props} /> : null}
        {toolbar ? <PageToolbar {...toolbar.props} /> : null}
        <div className="flex min-h-0 min-w-0 flex-1">
          {asideSide === "left" ? asidePanel : null}
          <PageBody {...bodyProps}>{bodyChildren}</PageBody>
          {asideSide === "right" ? asidePanel : null}
        </div>
        {footer ? <PageFooter {...footer.props} /> : null}
      </div>
    );

    if (!chrome) return frame;

    return <Page className={cn("min-h-full", className)}>{frame}</Page>;
  },
  {
    Header: RecordViewHeader,
    Toolbar: RecordViewToolbar,
    Body: RecordViewBody,
    Aside: RecordViewAside,
    Footer: RecordViewFooter,
  },
) satisfies RecordViewComponent;
