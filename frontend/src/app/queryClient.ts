import { QueryClient } from "@tanstack/react-query";

// One client for the app. Reads are snapshots of a continuously-running being —
// short stale time keeps memory/goals/audit reasonably fresh without hammering
// the API, while the WS streams carry the truly-live surfaces (consciousness +
// state). A 401 (token rejected) must NOT be retried — it routes back to the
// gate instead (see http.ts / TokenGate).
export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 10_000,
        retry: (failureCount, error) => {
          const status = (error as { status?: number } | null)?.status;
          if (status === 401 || status === 403) {
            return false;
          }
          return failureCount < 2;
        },
        refetchOnWindowFocus: false,
      },
    },
  });
}
