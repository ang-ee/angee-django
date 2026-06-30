import {
  Component,
  Suspense,
  type ReactElement,
  type ReactNode,
} from "react";

export interface LazyBoundaryProps {
  /** Shown while a lazy child's chunk is loading. */
  pending: ReactNode;
  /**
   * Shown when a child throws — e.g. a chunk fails to load (a stale deploy) or a
   * renderer crashes — so the lazy region degrades to a fallback instead of
   * throwing to the nearest route/app boundary and blanking far more than it.
   * Defaults to `pending`.
   */
  fallback?: ReactNode;
  /** When this changes, the error state resets so the child retries. */
  resetKey?: string | number;
  children: ReactNode;
}

/**
 * The one owner for mounting code-split (lazy) content: a Suspense boundary for
 * the loading state paired with an error boundary for failures. Every deferred
 * surface composes it — the preview pane, lazy widgets, the spotlight, the
 * relation picker — so the Suspense+error pairing (and the "degrade, don't
 * blank" guarantee) lives once instead of being re-hand-rolled per call site.
 */
export function LazyBoundary({
  pending,
  fallback,
  resetKey,
  children,
}: LazyBoundaryProps): ReactElement {
  return (
    <LazyErrorBoundary fallback={fallback ?? pending} resetKey={resetKey}>
      <Suspense fallback={pending}>{children}</Suspense>
    </LazyErrorBoundary>
  );
}

interface LazyErrorBoundaryProps {
  fallback: ReactNode;
  /** When this changes the boundary resets so a new child retries. */
  resetKey?: string | number;
  children: ReactNode;
}

class LazyErrorBoundary extends Component<
  LazyErrorBoundaryProps,
  { failed: boolean }
> {
  override state = { failed: false };

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }

  override componentDidUpdate(previous: LazyErrorBoundaryProps): void {
    if (this.state.failed && previous.resetKey !== this.props.resetKey) {
      this.setState({ failed: false });
    }
  }

  override render(): ReactNode {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}
