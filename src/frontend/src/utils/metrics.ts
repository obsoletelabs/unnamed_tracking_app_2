import type { MetricHistogram, MetricsSnapshot } from "../services/metrics";

export function metricKey(labels: Record<string, string>): string {
  return JSON.stringify(
    Object.entries(labels).sort(([a], [b]) => a.localeCompare(b)),
  );
}
export function histogramQuantile(
  row: MetricHistogram,
  quantile: number,
): number | null {
  if (row.count <= 0) return null;
  const rank = row.count * quantile;
  let lower = 0,
    previous = 0;
  for (const [bound, count] of [...row.buckets].sort(
    (a, b) =>
      (a[0] === "+Inf" ? Infinity : Number(a[0])) -
      (b[0] === "+Inf" ? Infinity : Number(b[0])),
  )) {
    const upper = bound === "+Inf" ? Infinity : Number(bound);
    if (count >= rank) {
      if (!Number.isFinite(upper)) return lower;
      return count === previous
        ? upper
        : lower + ((upper - lower) * (rank - previous)) / (count - previous);
    }
    lower = upper;
    previous = count;
  }
  return null;
}
export function histogramWindow(
  current: MetricHistogram,
  previous?: MetricHistogram,
): MetricHistogram {
  if (!previous || current.count < previous.count) return current;
  const buckets = new Map(previous.buckets);
  return {
    ...current,
    count: current.count - previous.count,
    sum: Math.max(0, current.sum - previous.sum),
    buckets: current.buckets.map(([bound, value]) => [
      bound,
      Math.max(0, value - (buckets.get(bound) ?? 0)),
    ]),
  };
}
export function metricHistograms(
  current: MetricsSnapshot | null,
  previous: MetricsSnapshot | null,
  family: string,
  route = "",
  method = "",
): MetricHistogram[] {
  const rows =
    previous?.started_at === current?.started_at
      ? (previous?.histograms?.[family] ?? [])
      : [];
  const before = new Map(rows.map((row) => [metricKey(row.labels), row]));
  return (current?.histograms?.[family] ?? [])
    .filter(
      (row) =>
        (!route || row.labels.route === route) &&
        (!method || row.labels.method === method),
    )
    .map((row) => histogramWindow(row, before.get(metricKey(row.labels))));
}
export function mergeHistograms(rows: MetricHistogram[]): MetricHistogram {
  const buckets = new Map<string, number>();
  for (const row of rows)
    for (const [bound, count] of row.buckets)
      buckets.set(bound, (buckets.get(bound) ?? 0) + count);
  return {
    labels: {},
    count: rows.reduce((sum, row) => sum + row.count, 0),
    sum: rows.reduce((sum, row) => sum + row.sum, 0),
    buckets: [...buckets],
  };
}
