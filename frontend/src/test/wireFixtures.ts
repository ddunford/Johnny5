import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

/**
 * Loader for the captured wire fixtures — the load-bearing input to the service
 * contract tests (TASK-5b.5 / `/plan-review` Step 7c).
 *
 * The fixtures live at the REPO ROOT (`tests/fixtures/wire/`), OUTSIDE this frontend
 * package, because they are the shared source of truth captured from the REAL 5a API
 * + the two WS streams (`tests/api/test_wire_fixtures.py`: TASK-5a.9 captured the REST
 * envelopes; the 5b WS-frame capture added the `{type,id,ts,…}` socket frames). A
 * contract test feeds an adapter the LITERAL file here and asserts the projection, so
 * a server-side rename can't silently ship behind a green hand-authored mock.
 *
 * Read with `node:fs` (not a Vite import) so it works regardless of Vite's `fs.allow`
 * root — the files are genuinely outside the package and must NOT be copied in (a copy
 * would drift from the captured wire). Re-run the capture to refresh them, never edit.
 */

// frontend/src/test/  ->  ../../../  ->  <repo root>/tests/fixtures/wire
const WIRE_DIR = resolve(dirname(fileURLToPath(import.meta.url)), "../../../tests/fixtures/wire");

/** Parse a captured wire fixture by name (`.json` optional), e.g. `loadWire("state")`. */
export function loadWire<T = unknown>(name: string): T {
  const file = name.endsWith(".json") ? name : `${name}.json`;
  return JSON.parse(readFileSync(resolve(WIRE_DIR, file), "utf-8")) as T;
}

/**
 * The populated + empty-state fixture pair for one wire surface — the two inputs every
 * adapter contract test must cover (Step 7c: empty-state is where the `undefined`/null
 * projection bugs hide). `empty` is `undefined` for surfaces with no empty variant.
 */
export function loadWirePair<T = unknown>(name: string): { populated: T; empty: T | undefined } {
  return { populated: loadWire<T>(name), empty: tryLoadWire<T>(`${name}.empty`) };
}

/** Like {@link loadWire} but returns `undefined` instead of throwing if the file is absent. */
export function tryLoadWire<T = unknown>(name: string): T | undefined {
  try {
    return loadWire<T>(name);
  } catch {
    return undefined;
  }
}
