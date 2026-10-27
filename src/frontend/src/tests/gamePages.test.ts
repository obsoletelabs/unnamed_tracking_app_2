import { afterEach, expect, it, vi } from "vitest";
import {
  fetchAllGamePages,
  fetchViewportGamePage,
  GAMES_PAGE_SIZE,
} from "../services/gamePages";

afterEach(() => vi.unstubAllGlobals());

const rows = (from: number, count: number) =>
  Array.from({ length: count }, (_, i) => ({ id: from + i }));

function serve(total: number, sendTotal: boolean) {
  const calls: number[] = [];
  let inFlight = 0;
  let peak = 0;
  const fetcher = vi.fn(async (url: string) => {
    const skip = Number(new URL(url, "http://x").searchParams.get("skip"));
    calls.push(skip);
    inFlight += 1;
    peak = Math.max(peak, inFlight);
    await new Promise((resolve) => setTimeout(resolve, 5));
    inFlight -= 1;
    const body = rows(
      skip,
      Math.max(0, Math.min(GAMES_PAGE_SIZE, total - skip)),
    );
    return new Response(JSON.stringify(body), {
      headers: sendTotal ? { "X-Total-Count": String(total) } : {},
    });
  });
  vi.stubGlobal("fetch", fetcher);
  return { calls, peak: () => peak };
}

it("requests the remaining pages together once the total is known", async () => {
  const server = serve(GAMES_PAGE_SIZE * 5 + 7, true);
  const all = await fetchAllGamePages<{ id: number }>();
  expect(all).toHaveLength(GAMES_PAGE_SIZE * 5 + 7);
  expect(all.map((row) => row.id)).toEqual(all.map((_, index) => index));
  expect(server.calls).toHaveLength(6);
  expect(server.peak()).toBeGreaterThan(1);
  expect(server.peak()).toBeLessThanOrEqual(4);
});

it("reads only an adaptive viewport page and its status-count summary", async () => {
  const fetcher = vi.fn(async (url: string) => {
    const limit = Number(new URL(url, "http://x").searchParams.get("limit"));
    return new Response(JSON.stringify(rows(0, limit)), {
      headers: {
        "X-Total-Count": "5000",
        "X-Status-Counts": "playing:800,backlog:4200",
      },
    });
  });
  vi.stubGlobal("fetch", fetcher);
  const result = await fetchViewportGamePage<{ id: number }>(0);
  expect(result.games.length).toBeLessThan(1000);
  expect(result.total).toBe(5000);
  expect(result.statusCounts).toEqual({ playing: 800, backlog: 4200 });
  expect(fetcher).toHaveBeenCalledTimes(1);
});

it("sends selected status and favorites filters to the paged endpoint", async () => {
  const fetcher = vi.fn(
    async (_url: string) =>
      new Response(JSON.stringify(rows(0, 3)), {
        headers: {
          "X-Total-Count": "3",
          "X-Status-Counts": "playing:3,backlog:8",
        },
      }),
  );
  vi.stubGlobal("fetch", fetcher);
  const result = await fetchViewportGamePage<{ id: number }>(
    0,
    undefined,
    "3",
    "playing",
    true,
  );
  const requestedUrl = fetcher.mock.calls[0]?.[0] as string;
  const params = new URL(requestedUrl, "http://x").searchParams;
  expect(params.get("search")).toBe("3");
  expect(params.get("status")).toBe("PLAYING");
  expect(params.get("favorite")).toBe("true");
  expect(result.total).toBe(3);
  expect(result.statusCounts).toEqual({ playing: 3, backlog: 8 });
});

it("sizes its first game page for a wide compact-card viewport", async () => {
  vi.resetModules();
  vi.stubGlobal("window", { innerWidth: 2560, innerHeight: 1440 });
  vi.stubGlobal("navigator", {});
  vi.stubGlobal("localStorage", { getItem: () => "compact" });
  const fetcher = vi.fn(async (url: string) => {
    const limit = Number(new URL(url, "http://x").searchParams.get("limit"));
    return new Response(JSON.stringify(rows(0, limit)), {
      headers: { "X-Total-Count": "5000" },
    });
  });
  vi.stubGlobal("fetch", fetcher);

  const { fetchViewportGamePage: fetchViewport } =
    await import("../services/gamePages");
  const result = await fetchViewport<{ id: number }>(0);
  const requestedUrl = fetcher.mock.calls[0]?.[0];
  expect(new URL(requestedUrl!, "http://x").searchParams.get("limit")).toBe(
    "208",
  );
  expect(result.games).toHaveLength(208);
});

it("does not ask for more pages when the first one holds everything", async () => {
  const server = serve(GAMES_PAGE_SIZE - 1, true);
  expect(await fetchAllGamePages()).toHaveLength(GAMES_PAGE_SIZE - 1);
  expect(server.calls).toEqual([0]);
});

it("does not request an empty extra page when the total is an exact multiple", async () => {
  const server = serve(GAMES_PAGE_SIZE * 2, true);
  expect(await fetchAllGamePages()).toHaveLength(GAMES_PAGE_SIZE * 2);
  expect(server.calls).toEqual([0, GAMES_PAGE_SIZE]);
});

it("falls back to sequential reads for a server without the total header", async () => {
  const server = serve(GAMES_PAGE_SIZE * 2 + 3, false);
  expect(await fetchAllGamePages()).toHaveLength(GAMES_PAGE_SIZE * 2 + 3);
  expect(server.calls).toEqual([0, GAMES_PAGE_SIZE, GAMES_PAGE_SIZE * 2]);
  expect(server.peak()).toBe(1);
});

it("fails the whole read when any page fails", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) =>
      url.includes("skip=0")
        ? new Response(JSON.stringify(rows(0, GAMES_PAGE_SIZE)), {
            headers: { "X-Total-Count": String(GAMES_PAGE_SIZE * 2) },
          })
        : new Response("no", { status: 500, statusText: "Server Error" }),
    ),
  );
  await expect(fetchAllGamePages()).rejects.toThrow(
    /Failed to fetch games: 500/,
  );
});
