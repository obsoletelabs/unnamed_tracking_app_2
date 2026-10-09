import { afterEach, expect, it, vi } from "vitest";
import { effectScope, ref } from "vue";
import {
  useMetadataSearch,
  matchesMetadataQuery,
} from "../composables/useMetadataSearch";
import type { MetadataCandidate, MetadataEvent } from "../services/metadata";

class Stream {
  static instances: Stream[] = [];
  readyState = 1;
  onerror: (() => void) | null = null;
  listeners = new Map<string, (message: { data: string }) => void>();
  readonly url: string;
  constructor(url: string) {
    this.url = url;
    Stream.instances.push(this);
  }
  addEventListener(
    name: string,
    listener: (message: { data: string }) => void,
  ) {
    this.listeners.set(name, listener);
  }
  close() {
    this.readyState = 2;
  }
  emit(event: MetadataEvent) {
    this.listeners.get(event.event)?.({ data: JSON.stringify(event) });
  }
}
function candidate(title: string, id = title): MetadataCandidate {
  return {
    id,
    title,
    rank: 0,
    external_id: id,
    provider: "test.provider",
    provider_name: "Test",
    providers: ["test.provider"],
    provider_ids: {},
    media_type: "game",
    year: null,
    alternate_titles: [],
    metadata: {},
    assets: [],
  };
}
const scopes: ReturnType<typeof effectScope>[] = [];
afterEach(() => {
  scopes.forEach((scope) => scope.stop());
  scopes.length = 0;
  Stream.instances = [];
  vi.useRealTimers();
  vi.unstubAllGlobals();
});
function setup() {
  vi.useFakeTimers();
  vi.stubGlobal("EventSource", Stream);
  const fetch = vi.fn(async (url: string, options: RequestInit) => {
    if (options.method === "DELETE") return new Response(null, { status: 204 });
    const body = JSON.parse(String(options.body));
    if (url.endsWith("/selection"))
      return new Response(JSON.stringify(candidate("Caramel", "new")));
    return new Response(
      JSON.stringify({
        id: body.query,
        query: body.query,
        media_type: "game",
        results: [],
        state: "searching",
        last_event_id: 0,
      }),
    );
  });
  vi.stubGlobal("fetch", fetch);
  const query = ref("car");
  const scope = effectScope();
  scopes.push(scope);
  const search = scope.run(() => useMetadataSearch(query, "game"))!;
  return { query, search, fetch };
}
function add(
  stream: Stream,
  result: MetadataCandidate,
  id: number,
  sessionId = "car",
) {
  stream.emit({ id, session_id: sessionId, event: "result_added", result });
}

it("ignores punctuation during cheap local filtering, including alternate titles", () => {
  expect(matchesMetadataQuery(candidate("Car: The Game"), "car the")).toBe(
    true,
  );
  expect(matchesMetadataQuery(candidate("Caramel Sauce"), "cara")).toBe(true);
  expect(matchesMetadataQuery(candidate("Car Mechanics"), "cara")).toBe(false);
  expect(
    matchesMetadataQuery(
      { ...candidate("Other"), alternate_titles: ["Car-a-mel"] },
      "caramel",
    ),
  ).toBe(true);
});

it("filters on the keystroke before a request and restores candidates on backspacing", async () => {
  const { query, search, fetch } = setup();
  await search.search();
  const stream = Stream.instances[0]!;
  add(stream, candidate("Car Mechanics"), 1);
  add(stream, candidate("Caramel Sauce"), 2);
  expect(search.results.value).toHaveLength(2);
  query.value = "cara";
  expect(search.results.value.map((item) => item.title)).toEqual([
    "Caramel Sauce",
  ]);
  expect(
    fetch.mock.calls.filter(([, options]) => options.method === "POST"),
  ).toHaveLength(1);
  query.value = "car";
  expect(search.results.value).toHaveLength(2);
});

it("rejects old events immediately while accepting newly arriving current matches", async () => {
  const { query, search } = setup();
  await search.search();
  const old = Stream.instances[0]!;
  query.value = "cara";
  add(old, candidate("Caravan stale"), 1);
  expect(search.results.value).toHaveLength(0);
  await vi.advanceTimersByTimeAsync(250);
  const current = Stream.instances[1]!;
  add(current, candidate("Caramel new"), 1, "cara");
  add(current, candidate("Car Mechanics"), 2, "cara");
  expect(search.results.value.map((item) => item.title)).toEqual([
    "Caramel new",
  ]);
  expect(search.searching.value).toBe(true);
  current.emit({ id: 3, session_id: "cara", event: "search_completed" });
  expect(search.searching.value).toBe(false);
});

it("uses backend ranking and ignores duplicate or out-of-order events", async () => {
  const { search } = setup();
  await search.search();
  const stream = Stream.instances[0]!;
  add(stream, candidate("Car A", "a"), 1);
  add(stream, candidate("Car B", "b"), 2);
  stream.emit({
    id: 3,
    session_id: "car",
    event: "ranking_updated",
    candidate_ids: ["b", "a"],
  });
  add(stream, candidate("Car B stale", "b"), 2);
  expect(search.results.value.map((item) => item.id)).toEqual(["b", "a"]);
  expect(search.results.value[0]?.title).toBe("Car B");
});

it("keeps receiving text-only year patches after identity search finishes", async () => {
  const { search } = setup();
  await search.search();
  const stream = Stream.instances[0]!;
  const results = Array.from({ length: 7 }, (_, index) => ({
    ...candidate(`Car ${index}`),
    rank: index,
  }));
  results.forEach((result, index) => add(stream, result, index + 1));
  stream.emit({ id: 8, session_id: "car", event: "search_completed" });
  expect(search.searching.value).toBe(false);
  for (const [offset, index] of [4, 5, 0, 6, 1, 2, 3].entries()) {
    stream.emit({
      id: 9 + offset,
      session_id: "car",
      event: "result_updated",
      result: {
        ...results[index]!,
        year: 2000 + index,
        metadata: { year: 2000 + index, description: "Preloaded text" },
      },
    });
  }
  expect(search.results.value.map((result) => result.year)).toEqual([
    2000, 2001, 2002, 2003, 2004, 2005, 2006,
  ]);
  expect(
    search.results.value.every((result) => result.assets.length === 0),
  ).toBe(true);
});

it("retains preloaded text when a changed query returns the same bare identity", async () => {
  const { query, search, fetch } = setup();
  await search.search();
  const retained = {
    ...candidate("Caramel", "same"),
    year: 2011,
    metadata: { year: 2011, description: "Preloaded text", genres: ["Puzzle"] },
    assets: [
      {
        kind: "key_art" as const,
        url: "https://images.example/cover",
        width: null,
        height: null,
      },
    ],
  };
  add(Stream.instances[0]!, retained, 1);
  query.value = "cara";
  await vi.advanceTimersByTimeAsync(250);
  add(
    Stream.instances[1]!,
    {
      ...candidate("Caramel", "same"),
      metadata: { description: null, genres: [] },
    },
    1,
    "cara",
  );
  expect(search.results.value[0]?.year).toBe(2011);
  expect(search.results.value[0]?.metadata).toEqual(retained.metadata);
  expect(search.results.value[0]?.assets).toEqual([]);
  const original = fetch.getMockImplementation()!;
  fetch.mockImplementation(async (url, options) =>
    url.endsWith("/selection")
      ? new Response(JSON.stringify(candidate("Caramel", "same")))
      : original(url, options),
  );
  const selection = await search.select(search.results.value[0]!);
  expect(selection.metadata).toEqual(retained.metadata);
  expect(selection.year).toBe(2011);
});

it("accepts fresh text and years while retaining fields a partial update omits", async () => {
  const { search } = setup();
  await search.search();
  const stream = Stream.instances[0]!;
  add(
    stream,
    {
      ...candidate("Caramel", "same"),
      year: 2011,
      metadata: { year: 2011, description: "Old text", genres: ["Puzzle"] },
    },
    1,
  );
  stream.emit({
    id: 2,
    session_id: "car",
    event: "result_updated",
    result: {
      ...candidate("Caramel", "same"),
      year: 2012,
      metadata: { year: 2012, description: "Fresh text", genres: [] },
    },
  });
  expect(search.results.value[0]?.metadata).toEqual({
    year: 2012,
    description: "Fresh text",
    genres: ["Puzzle"],
  });
  expect(search.results.value[0]?.year).toBe(2012);
});

it("does not carry cached text across conflicting provider identities", async () => {
  const { search } = setup();
  await search.search();
  const stream = Stream.instances[0]!;
  add(stream, { ...candidate("Caramel", "same"), year: 2011 }, 1);
  add(
    stream,
    { ...candidate("Caramel", "same"), external_id: "different-edition" },
    2,
  );
  expect(search.results.value[0]?.year).toBeNull();
});
