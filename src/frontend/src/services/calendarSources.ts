export interface CalendarSource {
  id: string;
  name: string;
  color: string;
  hostname: string;
  enabled: boolean;
  refresh_minutes: number;
  last_attempt_at: number | null;
  last_success_at: number | null;
  next_refresh_at: number;
  last_status: string;
  warnings: string[];
  consecutive_failures: number;
}

export interface CalendarTime {
  kind: "date" | "floating" | "zoned" | "utc";
  value: string;
  tzid?: string;
}

export interface ExternalCalendarEvent {
  event_id: string;
  uid: string;
  recurrence_key: string;
  title: string;
  note: string;
  location: string;
  url: string;
  start: CalendarTime;
  original_start: CalendarTime;
  duration_seconds: number;
  event_date: string;
  event_time: string | null;
  end_date: string;
  end_time: string | null;
  last_date: string;
  recurring: boolean;
  series_override: boolean;
  occurrence_override: boolean;
  source: CalendarSource;
}

export interface SourceInput {
  name: string;
  url: string;
  color: string;
  enabled: boolean;
  refresh_minutes: number;
}

export interface OccurrenceTarget {
  event_id: string;
  recurrence_key: string;
  occurrence_date: string;
  timezone: string;
}
export interface OverrideInput extends OccurrenceTarget {
  scope: "occurrence" | "series";
  fields: {
    title?: string;
    note?: string;
    location?: string;
    start?: CalendarTime;
    duration_seconds?: number;
  };
}

const base = "/api/calendar/sources";
async function request<T>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const response = await fetch(base + path, {
    method,
    headers:
      body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    const data: { detail?: string | { code?: string; message?: string } } =
      await response.json().catch(() => ({}));
    const detail = data.detail;
    // Never echo a validation payload that might contain the supplied secret URL.
    throw new Error(
      typeof detail === "string"
        ? detail
        : detail && !Array.isArray(detail) && detail.message
          ? `${detail.message} (${detail.code ?? response.status})`
          : `Calendar request failed (${response.status}).`,
    );
  }
  return response.status === 204 ? (undefined as T) : response.json();
}
export const fetchCalendarSources = () =>
  request<{ remote_enabled: boolean; sources: CalendarSource[] }>("");
export const createCalendarSource = (input: SourceInput) =>
  request<CalendarSource>("", "POST", input);
export const updateCalendarSource = (id: string, input: Partial<SourceInput>) =>
  request<CalendarSource>(`/${id}`, "PATCH", input);
export const deleteCalendarSource = (id: string) =>
  request<void>(`/${id}`, "DELETE");
export const refreshCalendarSource = (id: string) =>
  request<CalendarSource>(`/${id}/refresh`, "POST");
export const fetchSourceEvents = (
  source: CalendarSource,
  start: string,
  end: string,
) =>
  request<{
    events: Omit<ExternalCalendarEvent, "source">[];
    warnings: string[];
  }>(
    `/${source.id}/events?${new URLSearchParams({ start, end, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC" })}`,
  );
export const setCalendarOverride = (id: string, input: OverrideInput) =>
  request(`/${id}/overrides`, "PUT", input);
export const clearCalendarOverride = (id: string, input: OverrideInput) =>
  request(`/${id}/overrides`, "DELETE", input);
export const copyExternalEvent = (id: string, input: OccurrenceTarget) =>
  request<{ id: string }>(`/${id}/copy`, "POST", input);
export function occurrenceTarget(
  event: ExternalCalendarEvent,
): OccurrenceTarget {
  return {
    event_id: event.event_id,
    recurrence_key: event.recurrence_key,
    occurrence_date: event.original_start.value.slice(0, 10),
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
  };
}

export function sourceStatus(source: CalendarSource): string {
  if (!source.enabled) return "Disabled";
  if (source.last_status === "updated") return "Up to date";
  if (source.last_status === "not_modified") return "Unchanged";
  if (source.last_status === "never") return "Not refreshed yet";
  return `Refresh failed (${source.last_status}). Last valid events are kept.`;
}
