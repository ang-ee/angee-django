import * as React from "react";

import {
  ControlBand,
  controlBandItemClassName,
} from "../../layouts/ControlBand";
import { ResourceToolbar, type ResourceToolbarProps } from "../../toolbars";
import { ResourceCreateButton } from "../../toolbars/ResourceToolbar";
import { cn } from "../../lib/cn";
import { ErrorBanner } from "../../fragments/ErrorBanner";
import { Button } from "../../ui/button";
import { skeletonVariants } from "../../ui/skeleton";
import { SectionHeading } from "../form/SectionHeading";
import { useUiT } from "../../i18n";
import {
  ListLoadingFooter,
  SelectionBar,
} from "./resource-view-list-body";
import type { ListChrome, ResourceCollectionPresentation } from "./resource-view-types";

export interface ResourceListFrameSelection {
  count: number;
  onClear: () => void;
  onDelete?: () => void;
  deletePending?: boolean;
  actions?: React.ReactNode;
}

export interface ResourceListFrameProps {
  toolbar: ResourceToolbarProps;
  className?: string;
  presentation?: ResourceCollectionPresentation;
  /** Compact embedded chrome: the heading row carries create and toolbar actions
   * in place of the control band, and the toolbar row renders inline only for
   * the controls `toolbar.chrome` leaves on. */
  compact?: boolean;
  selection?: ResourceListFrameSelection;
  error?: Error | null;
  onRetry?: () => void;
  summary?: string;
  heading?: ListChrome["heading"];
  loadingFooter?: boolean;
  fetching?: boolean;
  /** Whether this render already has rows; retain settled content only for a gap. */
  hasRows?: boolean;
  children: React.ReactNode;
  overlays?: React.ReactNode;
}

/** Shared rendered frame for prepared list surfaces. */
export function ResourceListFrame({
  toolbar,
  className,
  presentation = "page",
  compact = false,
  selection,
  error = null,
  onRetry,
  summary,
  heading,
  loadingFooter = false,
  fetching = false,
  hasRows = true,
  children,
  overlays,
}: ResourceListFrameProps): React.ReactElement {
  const t = useUiT();
  const previousContent = React.useRef<React.ReactNode>(null);
  React.useEffect(() => {
    if (!fetching && !error) previousContent.current = hasRows ? children : null;
  }, [children, error, fetching, hasRows]);
  const retaining = fetching && !hasRows && previousContent.current !== null;
  const total = toolbar.pager.total;
  // An embedded heading row owns the collection's verbs; without one they stay in the toolbar.
  const headingVerbs = compact && Boolean(heading);
  const inlineToolbar: ResourceToolbarProps = headingVerbs
    ? { ...toolbar, onCreate: undefined, actions: undefined, utilityActions: undefined }
    : { ...toolbar, utilityActions: compact ? undefined : toolbar.utilityActions };
  const showInlineToolbar = toolbar.chrome?.search !== false || toolbar.chrome?.pager !== false
    || toolbar.chrome?.viewSwitcher !== false
    || (!headingVerbs && Boolean(toolbar.onCreate || toolbar.actions));
  return (
    <>
      {heading ? (
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2 border-b border-border-subtle px-3 py-2">
          <SectionHeading className="flex-1" as="h2"
            label={heading.label}
            count={total === undefined
              ? fetching ? <>· <span aria-hidden="true" className={skeletonVariants({ shape: "text", size: "sm", className: "inline-block w-6" })} /></> : undefined
              : toolbar.pagerTotalUnit === "groups" ? `· ${t("list.groupCount", { count: total })}` : `· ${total}`}
            hint={heading.hint == null ? undefined : <>· {heading.hint}</>}
            audience={heading.audience == null ? undefined : <>· {heading.audience}</>}
          />
          {headingVerbs ? toolbar.actions : null}
          {headingVerbs && toolbar.onCreate ? <ResourceCreateButton label={toolbar.createLabel} onCreate={toolbar.onCreate} /> : null}
        </div>
      ) : null}
      {compact ? (
        showInlineToolbar ? <ResourceToolbar {...inlineToolbar} /> : null
      ) : (
        /* Embedded: the band pads like a table cell, so the box starts where the first header's text does. */
        <ControlBand wrap={toolbar.wrap} className={presentation === "embedded" ? "px-3" : undefined}>
          <ResourceToolbar
            {...toolbar}
            className={cn(
              controlBandItemClassName,
              toolbar.wrap && "h-auto",
              toolbar.className,
            )}
          />
        </ControlBand>
      )}
      <div
        data-resource-presentation={presentation}
        aria-busy={fetching}
        className={cn(
          "resource-list-frame flex min-w-0 flex-col bg-sheet",
          retaining && "pointer-events-none opacity-50",
          presentation === "embedded"
            ? "overflow-visible"
            : presentation === "workspace"
              ? "min-h-0 flex-1 overflow-hidden"
              : "h-full min-h-0 overflow-hidden",
          className,
        )}
      >
        {summary ? (
          <p className="border-b border-border-subtle px-3 py-2 text-2xs text-fg-muted">
            {summary}
          </p>
        ) : null}
        {selection && selection.count > 0 ? (
          <SelectionBar
            count={selection.count}
            onClear={selection.onClear}
            onDelete={selection.onDelete}
            deletePending={selection.deletePending}
            actions={selection.actions}
          />
        ) : null}
        {error ? (
          <ErrorBanner
            description={error.message}
            actions={onRetry ? <Button size="sm" onClick={onRetry}>{t("collection.retry")}</Button> : undefined}
          />
        ) : retaining ? previousContent.current : children}
        {loadingFooter ? <ListLoadingFooter /> : null}
        {overlays}
      </div>
    </>
  );
}
