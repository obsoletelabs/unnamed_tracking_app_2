import { expect, it } from "vitest";
import {
  histogramQuantile,
  histogramWindow,
  mergeHistograms,
  metricHistograms,
} from "../utils/metrics";
import type { MetricHistogram, MetricsSnapshot } from "../services/metrics";
const row: MetricHistogram = {
  labels: { method: "GET", route: "/api/example/{id}" },
  count: 10,
  sum: 0.4,
  buckets: [
    ["0.01", 5],
    ["0.1", 10],
    ["+Inf", 10],
  ],
};
it("interpolates histogram percentiles and handles no observations", () => {
  expect(histogramQuantile(row, 0.5)).toBeCloseTo(0.01);
  expect(histogramQuantile(row, 0.95)).toBeCloseTo(0.091);
  expect(histogramQuantile({ ...row, count: 0 }, 0.99)).toBeNull();
});
it("subtracts cumulative windows and recognizes a counter reset", () => {
  const before = {
    ...row,
    count: 5,
    sum: 0.2,
    buckets: row.buckets.map(
      ([bound, count]) => [bound, count / 2] as [string, number],
    ),
  };
  expect(histogramWindow(row, before).count).toBe(5);
  expect(histogramWindow(before, row)).toEqual(before);
  expect(mergeHistograms([row, row]).count).toBe(20);
});
it("filters routes/methods and refuses to subtract another worker lifetime", () => {
  const current: MetricsSnapshot = {
    enabled: true,
    started_at: 2,
    histograms: { http: [row] },
  };
  const previous: MetricsSnapshot = { ...current, started_at: 1 };
  expect(metricHistograms(current, previous, "http")[0]?.count).toBe(10);
  expect(metricHistograms(current, current, "http")[0]?.count).toBe(0);
  expect(metricHistograms(current, null, "http", "/api/other")).toEqual([]);
  expect(metricHistograms(current, null, "http", "", "POST")).toEqual([]);
});
