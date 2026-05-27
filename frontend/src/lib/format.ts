/** Shared formatting helpers for timestamps and numbers across the panels. */

/** Format an ISO timestamp as a short local time, or "—" if absent/invalid. */
export function formatTime(iso: string | null | undefined): string {
  if (!iso) {
    return "—";
  }
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return "—";
  }
  return date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

/** Format an ISO timestamp as a short local date + time, or "—". */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) {
    return "—";
  }
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return "—";
  }
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Format a 0..1 ratio as a whole-number percentage string, e.g. 0.66 → "66%". */
export function formatPercent(value: number): string {
  return `${Math.round(value * 100)}%`;
}
