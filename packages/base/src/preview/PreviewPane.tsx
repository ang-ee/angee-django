import {
  Component,
  Suspense,
  type ReactElement,
  type ReactNode,
} from "react";
import { usePreviews } from "@angee/sdk";

import { EmptyState } from "../fragments/EmptyState";
import { LoadingPanel } from "../fragments/LoadingPanel";
import { useBaseT } from "../i18n";
import { builtinPreviewProviders } from "./builtins";
import { displayMime } from "./model";
import {
  resolvePreviewProvider,
  type PreviewFile,
  type PreviewProvider,
} from "./registry";

export interface PreviewPaneProps {
  file: PreviewFile;
  /** Explicit content type; when omitted it is derived from the file. */
  mime?: string | null;
  /** Rendered when no provider resolves or a renderer crashes. */
  fallback?: ReactNode;
}

/**
 * The preview surface: resolves the matching renderer for a file's mime — from
 * the built-ins plus any addon-contributed providers on the runtime — and mounts
 * it inside a Suspense boundary (renderers may lazy-load their deps) and an error
 * boundary (a renderer crash degrades to the fallback, not a blank pane). With no
 * match it renders the fallback.
 */
export function PreviewPane({
  file,
  mime,
  fallback,
}: PreviewPaneProps): ReactElement {
  const t = useBaseT();
  const resolvedMime = mime ?? displayMime(file);
  // Addon-contributed providers first so one can override a built-in at an equal
  // priority (resolve sorts stably); built-ins are always available. The SDK
  // tracks only the contribution id for collision detection — base addons author
  // full `PreviewProvider`s, so this widening cast is a deliberate boundary.
  const runtimePreviews = usePreviews() as readonly PreviewProvider[];
  const provider = resolvePreviewProvider(
    [...runtimePreviews, ...builtinPreviewProviders],
    resolvedMime,
  );
  const empty = fallback ?? <EmptyState title={t("preview.unavailable")} />;
  if (!provider) return <>{empty}</>;

  const Renderer = provider.component;
  return (
    <PreviewErrorBoundary
      fallback={empty}
      resetKey={`${provider.id}:${file.url}`}
    >
      <Suspense fallback={<LoadingPanel message={t("preview.loading")} />}>
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
