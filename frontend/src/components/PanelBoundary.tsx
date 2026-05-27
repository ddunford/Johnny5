import type { ReactNode } from "react";
import { ErrorBoundary } from "./ErrorBoundary";

interface PanelBoundaryProps {
  name: string;
  children: ReactNode;
}

/**
 * Per-panel error isolation. Every routed panel is wrapped in one of these so a
 * thrown render in one panel cannot blank the rest of the app.
 */
export function PanelBoundary({ name, children }: PanelBoundaryProps): ReactNode {
  return <ErrorBoundary name={name}>{children}</ErrorBoundary>;
}
