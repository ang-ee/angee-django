import {
  Component,
  Suspense,
  type ReactElement,
  type ReactNode,
} from "react";

import { EmptyState } from "../fragments/EmptyState";
import { LoadingPanel } from "../fragments/LoadingPanel";
import { displayMime } from "./model";
import { resolvePreviewProvider, type PreviewFile } from "./registry";

export interface PreviewPaneProps {
  file: PreviewFile;
  /** Explicit content type; when omitted it is derived from the file. */
  mime?: string | null;
  /** Rendered when no provider resolves or a renderer crashes. */
  fallback?: ReactNode;
}

/**
 * The preview surface: resolves the registered renderer for a file's mime and
 * mounts it inside a Suspense boundary (renderers may lazy-load their deps) and
 * an error boundary (a renderer crash degrades to the fallback, not a blank
 * pane). With no provider registered for the mime it renders the fallback.
 */
export function PreviewPane({
  file,
  mime,
  fallback,
}: PreviewPaneProps): ReactElement {
  const resolvedMime = mime ?? displayMime(file);
  const provider = resolvePreviewProvider(resolvedMime);
  const empty = fallback ?? <EmptyState title="No preview available" />;
  if (!provider) return <>{empty}</>;

  const Renderer = provider.component;
  return (
    <PreviewErrorBoundary
      fallback={empty}
      resetKey={`${provider.id}:${file.url}`}
    >
      <Suspense fallback={<LoadingPanel message="Loading preview…" />}>
        <Renderer file={file} mime={resolvedMime} />
      </Suspense>
    </PreviewErrorBoundary>
  );
}

interface PreviewErrorBoundaryProps {
  fallback: ReactNode;
  /** When this changes the boundary resets so a new file/provider retries. */
  resetKey: string;
  children: ReactNode;
}

class PreviewErrorBoundary extends Component<
  PreviewErrorBoundaryProps,
  { failed: boolean }
> {
  override state = { failed: false };

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }

  override componentDidUpdate(previous: PreviewErrorBoundaryProps): void {
    if (this.state.failed && previous.resetKey !== this.props.resetKey) {
      this.setState({ failed: false });
    }
  }

  override render(): ReactNode {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}
