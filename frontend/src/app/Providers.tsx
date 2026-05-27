import { useState, type ReactNode } from "react";
import { QueryClientProvider } from "@tanstack/react-query";
import { createQueryClient } from "./queryClient";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { GlobalFallback } from "@/components/GlobalFallback";

interface ProvidersProps {
  children: ReactNode;
}

/**
 * App-wide providers: the React Query client for request/response server state and
 * a global error boundary as the last line of defence beneath the per-panel ones.
 * (Zustand stores need no provider — they are module singletons.)
 */
export function Providers({ children }: ProvidersProps): ReactNode {
  // Create the client once per mount (survives re-renders, fresh per test).
  const [queryClient] = useState(createQueryClient);

  return (
    <ErrorBoundary name="app" fallback={GlobalFallback}>
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    </ErrorBoundary>
  );
}
