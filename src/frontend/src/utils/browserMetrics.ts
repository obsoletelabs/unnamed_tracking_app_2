export interface BrowserResource {
  path: string;
  kind: string;
  protocol: string;
  start: number;
  beforeSend: number | null;
  waiting: number | null;
  download: number;
  duration: number;
  server: number | null;
}
export interface BrowserLoad {
  responseEnd: number | null;
  domReady: number | null;
  load: number | null;
  marks: { name: string; time: number }[];
  bootstrapGap: number | null;
  resources: BrowserResource[];
}
export function browserLoad(
  source: Pick<Performance, "getEntriesByType">,
  origin: string,
): BrowserLoad {
  const navigation = source.getEntriesByType("navigation")[0] as
    PerformanceNavigationTiming | undefined;
  const marks = source
    .getEntriesByType("mark")
    .filter((entry) => entry.name.startsWith("uta."))
    .map((entry) => ({ name: entry.name, time: entry.startTime }));
  const all = (
    source.getEntriesByType("resource") as PerformanceResourceTiming[]
  ).filter((entry) => new URL(entry.name).origin === origin);
  const initial = all.filter((entry) =>
    /\/assets\/(?:index-|vendor-|app-shell-|rolldown-runtime-)/.test(
      new URL(entry.name).pathname,
    ),
  );
  const entry = marks.find((mark) => mark.name === "uta.bootstrap-start");
  return {
    responseEnd: navigation?.responseEnd ?? null,
    domReady: navigation?.domContentLoadedEventEnd || null,
    load: navigation?.loadEventEnd || null,
    marks,
    bootstrapGap:
      entry && initial.length
        ? Math.max(
            0,
            entry.time - Math.max(...initial.map((row) => row.responseEnd)),
          )
        : null,
    resources: all
      .map((row) => ({
        path: new URL(row.name).pathname.replace(
          /[0-9a-f]{8}-[0-9a-f-]{27,}/gi,
          ":id",
        ),
        kind: row.initiatorType,
        protocol: row.nextHopProtocol || "cache / unavailable",
        start: row.startTime,
        beforeSend:
          row.requestStart > 0
            ? Math.max(0, row.requestStart - row.fetchStart)
            : null,
        waiting:
          row.requestStart > 0
            ? Math.max(0, row.responseStart - row.requestStart)
            : null,
        download: Math.max(0, row.responseEnd - row.responseStart),
        duration: row.duration,
        server:
          row.serverTiming?.find((timing) => timing.name === "app")?.duration ??
          null,
      }))
      .sort((a, b) => (b.beforeSend ?? 0) - (a.beforeSend ?? 0))
      .slice(0, 100),
  };
}
