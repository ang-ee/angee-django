import * as React from "react";

import {
  ControlBand,
  controlBandItemClassName,
} from "../../layouts/ControlBand";
import { ResourceToolbar, type ResourceToolbarProps } from "../../toolbars";
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
  return (
    <>
      {heading ? <SectionHeading className="border-b border-border-subtle px-3 py-2" as="h2"
        label={heading.label}
        count={toolbar.pager.total === undefined
          ? fetching ? <>· <span aria-hidden="true" className={skeletonVariants({ shape: "text", size: "sm", className: "inline-block w-6" })} /></> : undefined
          : `· ${toolbar.pager.total}`}
        hint={heading.hint == null ? undefined : <>· {heading.hint}</>}
        audience={heading.audience == null ? undefined : <>· {heading.audience}</>}
      /> : null}
      <ControlBand wrap={toolbar.wrap}>
        <ResourceToolbar
          {...toolbar}
          className={cn(
            controlBandItemClassName,
            toolbar.wrap && "h-auto",
            toolbar.className,
          )}
        />
      </ControlBand>
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
