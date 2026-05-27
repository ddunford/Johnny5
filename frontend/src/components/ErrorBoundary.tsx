import { Component, type ErrorInfo, type ReactNode } from "react";

interface ErrorBoundaryProps {
  /** Human label for the boundary (e.g. the panel name) — shown in the fallback. */
  name: string;
  children: ReactNode;
  /** Optional custom fallback; defaults to {@link DefaultFallback}. */
  fallback?: (error: Error, reset: () => void, name: string) => ReactNode;
}

interface ErrorBoundaryState {
  error: Error | null;
}

/**
 * A reusable error boundary. Wrapping each panel in one means a single crashing
 * panel (e.g. a bad memory render) is isolated — the live consciousness view and
 * every other panel keep running (CLAUDE.md: "a dead panel must not kill the live
 * consciousness view"). React still requires a class component for this.
 */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Surface the crash for debugging without taking the app down. Never logs
    // tokens (they live only in sessionStorage / request headers, never props).
    console.error(`[panel:${this.props.name}] render error`, error, info.componentStack);
  }

  reset = (): void => {
    this.setState({ error: null });
  };

  render(): ReactNode {
    const { error } = this.state;
    if (error) {
      const fallback = this.props.fallback ?? defaultFallback;
      return fallback(error, this.reset, this.props.name);
    }
    return this.props.children;
  }
}

function defaultFallback(error: Error, reset: () => void, name: string): ReactNode {
  return (
    <div className="panel-error" role="alert">
      <p className="panel-error__title">“{name}” hit a problem</p>
      <p className="panel-error__detail">{error.message}</p>
      <button type="button" className="panel-error__retry" onClick={reset}>
        Retry
      </button>
    </div>
  );
}
