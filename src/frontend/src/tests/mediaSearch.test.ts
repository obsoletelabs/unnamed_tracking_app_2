import { afterEach, expect, it, vi } from "vitest";
import { effectScope, ref } from "vue";
import { useMediaSearch } from "../composables/useMediaSearch";
import type {
  MetadataCandidate,
  MetadataEvent,
  MetadataMediaType,
} from "../services/metadata";

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
  get id() {
    return this.url.split("/sessions/")[1]!.split("/")[0]!;
  }
}
function candidate(
  type: MetadataMediaType,
  title = "Dune",
  id = "same-identity",
): MetadataCandidate {
  return {
    id,
    title,
    media_type: type,
    rank: 0,
    year: null,
    external_id: id,
    provider: `provider-${type}`,
    provider_name: type,
    providers: [],
    provider_ids: { imdb: id },
    alternate_titles: [],
    metadata: {},
    assets: [],
  };
}
const scopes: ReturnType<typeof effectScope>[] = [];
afterEach(() => {
  scopes.splice(0).forEach((scope) => scope.stop());
  Stream.instances = [];
  vi.useRealTimers();
  vi.unstubAllGlobals();
});
function setup(unavailable?: string) {
  vi.useFakeTimers();
  vi.stubGlobal("EventSource", Stream);
  const fetch = vi.fn(async (url: string, options: RequestInit) => {
    if (options.method === "DELETE") return new Response(null, { status: 204 });
    if (url.endsWith("/selection"))
      return new Response(JSON.stringify(candidate("tv_show")));
    const body = JSON.parse(String(options.body));
    if (body.media_type === unavailable)
      return new Response(
        JSON.stringify({ detail: "Movie provider unavailable" }),
        { status: 503 },
      );
    return new Response(
      JSON.stringify({
        id: `${body.media_type}-${body.query}`,
        results: [],
        last_event_id: 0,
      }),
    );
  });
  vi.stubGlobal("fetch", fetch);
  const query = ref("du");
  const scope = effectScope();
  scopes.push(scope);
  const search = scope.run(() => useMediaSearch(query))!;
  return { query, search, fetch };
}
function add(stream: Stream, item: MetadataCandidate, id = 1) {
  stream.emit({
    id,
    session_id: stream.id,
    event: "result_added",
    result: item,
  });
}

it("shows fast results before other types finish and changes type filters without network requests", async () => {
  const { search, fetch } = setup();
  await search.search();
  const tv = Stream.instances.find((stream) =>
    stream.id.startsWith("tv_show"),
  )!;
  add(tv, candidate("tv_show", "Dune TV"));
  expect(search.results.value.map((item) => item.title)).toEqual(["Dune TV"]);
  expect(search.searching.value).toBe(true);
  const movie = Stream.instances.find((stream) =>
    stream.id.startsWith("movie"),
  )!;
  add(movie, candidate("movie", "Dune Movie"));
  const posts = fetch.mock.calls.filter(
    ([, options]) => options.method === "POST",
  ).length;
  search.filter.value = "movie";
  expect(search.results.value.map((item) => item.title)).toEqual([
    "Dune Movie",
  ]);
  search.filter.value = "all";
  expect(search.results.value).toHaveLength(2);
  expect(
    fetch.mock.calls.filter(([, options]) => options.method === "POST"),
  ).toHaveLength(posts);
});

it("retains successful types when another search session cannot start", async () => {
  const { search } = setup("movie");
  await search.search();
  add(
    Stream.instances.find((stream) => stream.id.startsWith("anime"))!,
    candidate("anime"),
  );
  expect(search.results.value[0]?.media_type).toBe("anime");
  expect(search.warnings.value).toEqual([
    "Movies: The server had a problem. Wait a moment and try again.",
  ]);
});

it("filters retained titles immediately and rejects old events on query changes", async () => {
  const { search, query, fetch } = setup();
  await search.search();
  const old = Stream.instances.find((stream) => stream.id.startsWith("movie"))!;
  add(old, candidate("movie", "Dune"));
  add(old, candidate("movie", "Dungeon", "different"), 2);
  query.value = "dune";
  expect(search.results.value.map((item) => item.title)).toEqual(["Dune"]);
  add(old, candidate("movie", "Dune stale", "stale"), 3);
  expect(search.results.value.map((item) => item.title)).toEqual(["Dune"]);
  expect(
    fetch.mock.calls.filter(([, options]) => options.method === "POST"),
  ).toHaveLength(3);
  query.value = "du";
  expect(search.results.value).toHaveLength(2);
});

it("selects the correct typed identity and stops the other sessions while preserving late enrichment", async () => {
  const { search, fetch } = setup();
  await search.search();
  for (const stream of Stream.instances)
    add(stream, candidate(stream.id.split("-du")[0] as MetadataMediaType));
  const selected = search.results.value.find(
    (item) => item.media_type === "tv_show",
  )!;
  await search.select(selected);
  expect(
    fetch.mock.calls.find(([url]) => url.endsWith("/selection"))?.[0],
  ).toContain("tv_show-du");
  const tv = Stream.instances.at(-1)!;
  tv.emit({
    id: 2,
    session_id: tv.id,
    event: "result_updated",
    result: {
      ...selected,
      year: 2024,
      metadata: { description: "Late details" },
    },
  });
  expect(search.selected.value?.metadata.description).toBe("Late details");
  expect(
    Stream.instances
      .filter((stream) => !stream.id.startsWith("tv_show"))
      .every((stream) => stream.readyState === 2),
  ).toBe(true);
  search.closeSelection();
  expect(search.selected.value).toBeNull();
  expect(Stream.instances.every((stream) => stream.readyState === 2)).toBe(
    true,
  );
});

it("stops all background work without losing locally retained results", async () => {
  const { search } = setup();
  await search.search();
  const old = Stream.instances[0]!;
  add(old, candidate("movie"));
  search.stop();
  expect(search.searching.value).toBe(false);
  expect(Stream.instances.every((stream) => stream.readyState === 2)).toBe(
    true,
  );
  add(old, candidate("movie", "Dune late", "late"), 2);
  expect(search.results.value).toHaveLength(1);
});
