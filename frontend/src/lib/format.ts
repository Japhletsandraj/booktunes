/** Display helpers shared across pages. */

/**
 * The server's taxonomy uses snake_case slugs (`science_fiction`,
 * `self_help`). Those are the values the API matches on, so they travel
 * unchanged in requests — this only makes them readable on screen.
 */
export function prettyLabel(slug: string): string {
  return slug.replace(/_/g, ' ');
}

export function formatDuration(ms: number | null | undefined): string {
  if (!ms) return '';
  const total = Math.round(ms / 1000);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
}

export function formatHours(seconds: number): string {
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
}
