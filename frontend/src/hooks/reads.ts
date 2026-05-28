/**
 * React Query read hooks over the typed service adapters. Each passes the query's
 * `AbortSignal` to the adapter so React Query can cancel in-flight requests, and
 * keys on any params that change the result (see {@link queryKeys}).
 */

import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { queryKeys } from "./queryKeys";
import { fetchState } from "@/services/stateApi";
import { fetchThoughts } from "@/services/thoughtsApi";
import { fetchEpisodes, fetchFacts, type MemoryQuery } from "@/services/memoryApi";
import { fetchGoals } from "@/services/goalsApi";
import { fetchSleeps } from "@/services/sleepsApi";
import { fetchAudit, type AuditQuery } from "@/services/auditApi";
import {
  fetchAuditActions,
  type ActionAudit,
  type ActionAuditQuery,
} from "@/services/auditActionsApi";
import { fetchSelf } from "@/services/selfApi";
import type {
  AuditEvent,
  Episode,
  Fact,
  GoalsView,
  SelfView,
  SleepLog,
  StateView,
  Thought,
} from "@/services/types";

/** REST state snapshot — the dashboard's initial paint before `/ws/state` (FC-8). */
export function useStateSnapshot(): UseQueryResult<StateView> {
  return useQuery({
    queryKey: queryKeys.state(),
    queryFn: ({ signal }) => fetchState(signal),
  });
}

export function useThoughts(limit?: number): UseQueryResult<Thought[]> {
  return useQuery({
    queryKey: queryKeys.thoughts(limit),
    queryFn: ({ signal }) => fetchThoughts(limit, signal),
  });
}

export function useEpisodes(query: MemoryQuery = {}): UseQueryResult<Episode[]> {
  return useQuery({
    queryKey: queryKeys.episodes(query),
    queryFn: ({ signal }) => fetchEpisodes(query, signal),
  });
}

export function useFacts(query: MemoryQuery = {}): UseQueryResult<Fact[]> {
  return useQuery({
    queryKey: queryKeys.facts(query),
    queryFn: ({ signal }) => fetchFacts(query, signal),
  });
}

export function useGoals(limit?: number): UseQueryResult<GoalsView> {
  return useQuery({
    queryKey: queryKeys.goals(limit),
    queryFn: ({ signal }) => fetchGoals(limit, signal),
  });
}

export function useSleeps(limit?: number): UseQueryResult<SleepLog[]> {
  return useQuery({
    queryKey: queryKeys.sleeps(limit),
    queryFn: ({ signal }) => fetchSleeps(limit, signal),
  });
}

export function useAudit(query: AuditQuery = {}): UseQueryResult<AuditEvent[]> {
  return useQuery({
    queryKey: queryKeys.audit(query),
    queryFn: ({ signal }) => fetchAudit(query, signal),
  });
}

/** The durable, Core-written action_log trail (`GET /api/v1/audit/actions`) — the
 * trustworthy record, distinct from the live bus feed in {@link useAudit}. */
export function useAuditActions(query: ActionAuditQuery = {}): UseQueryResult<ActionAudit[]> {
  return useQuery({
    queryKey: queryKeys.auditActions(query),
    queryFn: ({ signal }) => fetchAuditActions(query, signal),
  });
}

export function useSelf(notesLimit?: number): UseQueryResult<SelfView> {
  return useQuery({
    queryKey: queryKeys.self(notesLimit),
    queryFn: ({ signal }) => fetchSelf(notesLimit, signal),
  });
}
