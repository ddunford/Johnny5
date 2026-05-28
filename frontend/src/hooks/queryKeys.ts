import type { AuditQuery } from "@/services/auditApi";
import type { ActionAuditQuery } from "@/services/auditActionsApi";
import type { MemoryQuery } from "@/services/memoryApi";

/**
 * Centralised React Query keys, so cache entries are consistent and params that
 * change the result (search `q`, filters, limits) are part of the key.
 */
export const queryKeys = {
  state: () => ["state"] as const,
  thoughts: (limit?: number) => ["thoughts", limit ?? null] as const,
  episodes: (query: MemoryQuery) => ["memory", "episodes", query.q ?? null, query.limit ?? null] as const,
  facts: (query: MemoryQuery) => ["memory", "facts", query.q ?? null, query.limit ?? null] as const,
  goals: (limit?: number) => ["goals", limit ?? null] as const,
  sleeps: (limit?: number) => ["sleeps", limit ?? null] as const,
  audit: (query: AuditQuery) => ["audit", query.type ?? null, query.limit ?? null] as const,
  auditActions: (query: ActionAuditQuery) =>
    ["audit", "actions", query.verdict ?? null, query.limit ?? null] as const,
  self: (notesLimit?: number) => ["self", notesLimit ?? null] as const,
} as const;
