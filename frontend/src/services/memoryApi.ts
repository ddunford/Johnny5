/**
 * Adapters for the memory browser:
 *   `GET /api/v1/memory/episodes` (recent + free-text hybrid recall via `q`)
 *   `GET /api/v1/memory/facts`    (semantic facts + similarity recall via `q`)
 *
 * ServerEnvelope sources: `tests/fixtures/wire/memory_episodes.json` and
 * `memory_facts.json` (+ their `.empty.json` variants).
 */

import { get } from "./http";
import type { Episode, Fact } from "./types";

export interface EpisodesEnvelope {
  episodes: Episode[];
}

export interface FactsEnvelope {
  facts: Fact[];
}

export function adaptEpisodes(envelope: EpisodesEnvelope): Episode[] {
  return envelope.episodes ?? [];
}

export function adaptFacts(envelope: FactsEnvelope): Fact[] {
  return envelope.facts ?? [];
}

export interface MemoryQuery {
  q?: string;
  limit?: number;
}

export async function fetchEpisodes(
  { q, limit }: MemoryQuery = {},
  signal?: AbortSignal,
): Promise<Episode[]> {
  return adaptEpisodes(await get<EpisodesEnvelope>("/memory/episodes", { q, limit }, signal));
}

export async function fetchFacts(
  { q, limit }: MemoryQuery = {},
  signal?: AbortSignal,
): Promise<Fact[]> {
  return adaptFacts(await get<FactsEnvelope>("/memory/facts", { q, limit }, signal));
}
