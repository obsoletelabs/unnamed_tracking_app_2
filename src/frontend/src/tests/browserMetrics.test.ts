import { expect, it } from "vitest";
import { browserLoad } from "../utils/browserMetrics";
it("separates scheduling/transit and server work without retaining query secrets", () => {
  const resource = {
    name: "https://app.test/api/game/example?token=private",
    startTime: 10,
    fetchStart: 10,
    requestStart: 110,
    responseStart: 140,
    responseEnd: 142,
    duration: 132,
    initiatorType: "fetch",
    nextHopProtocol: "h2",
    serverTiming: [{ name: "app", duration: 25 }],
  } as unknown as PerformanceResourceTiming;
  const source = {
    getEntriesByType: (type: string): PerformanceEntry[] =>
      type === "resource" ? [resource] : [],
  };
  const report = browserLoad(source, "https://app.test");
  expect(report.resources[0]).toMatchObject({
    path: "/api/game/example",
    beforeSend: 100,
    waiting: 30,
    server: 25,
    download: 2,
  });
  expect(JSON.stringify(report)).not.toContain("private");
  expect(browserLoad(source, "https://other.test").resources).toEqual([]);
});
