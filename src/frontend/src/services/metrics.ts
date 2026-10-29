import { failedRequest } from "./apiError";

export interface MetricValue {
  labels: Record<string, string>;
  value: number;
}
export interface MetricHistogram {
  labels: Record<string, string>;
  count: number;
  sum: number;
  buckets: [string, number][];
}
export interface MetricsSnapshot {
  enabled: boolean;
  collected_at?: number;
  started_at?: number | null;
  scope?: "process" | "workers";
  histograms?: Record<string, MetricHistogram[]>;
  values?: Record<string, MetricValue[]>;
}
export async function fetchMetricsSnapshot(
  signal?: AbortSignal,
): Promise<MetricsSnapshot> {
  const response = await fetch("/api/settings/metrics/snapshot", {
    credentials: "include",
    signal,
    cache: "no-store",
    priority: "low",
  });
  if (!response.ok) throw await failedRequest(response);
  return response.json();
}
