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
