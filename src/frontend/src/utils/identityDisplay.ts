// Plain-language formatting for the identity views (#101).

export function hoursLabel(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "Unknown";
  if (seconds <= 0) return "Not played yet";
  const minutes = Math.round(seconds / 60);
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (!hours) return `${rest}m`;
  return rest ? `${hours}h ${rest}m` : `${hours}h`;
}

export function dayLabel(timestamp: number | null | undefined): string {
  if (!timestamp) return "Never";
  return new Date(timestamp * 1000).toLocaleDateString();
}

export function agoLabel(
  timestamp: number | null | undefined,
  now = Date.now() / 1000,
): string {
  if (!timestamp) return "never seen";
  const days = Math.floor((now - timestamp) / 86400);
  if (days < 1) return "today";
  if (days === 1) return "yesterday";
  if (days < 60) return `${days} days ago`;
  return `${Math.floor(days / 30)} months ago`;
}

export function sourceName(source: string | null | undefined): string {
  if (!source) return "Manual entry";
  return source.charAt(0).toUpperCase() + source.slice(1);
}

const OUTCOMES: Record<string, string> = {
  confirmed_same: "Merged by you",
  confirmed_separate: "Kept separate",
  automatic_same: "Matched automatically",
  unresolved: "Waiting for review",
};
export function outcomeLabel(outcome: string): string {
  return OUTCOMES[outcome] ?? outcome.replace(/_/g, " ");
}
