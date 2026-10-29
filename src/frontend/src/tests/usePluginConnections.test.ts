import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { effectScope } from "vue";
import {
  SYNC_POLL_MS,
  usePluginConnections,
} from "../composables/usePluginConnections";
import { pluginConnectionKey } from "../services/pluginConnections";
import type {
  PluginConnectionState,
  PluginConnectionSync,
} from "../services/pluginConnections";
import { tasks } from "../state/taskProgress";

const KEY = pluginConnectionKey("official.epic-games", "epic-games");

function sync(
  overrides: Partial<PluginConnectionSync> = {},
): PluginConnectionSync {
  return {
    available: true,
    unavailable_reason: "",
    running: false,
    paused: false,
    status: "idle",
    last_run_at: null,
    last_summary: "",
    ...overrides,
  };
}

function connection(
  syncState: PluginConnectionSync | null = sync(),
): PluginConnectionState {
  return {
    plugin_id: "official.epic-games",
    connection_id: "epic-games",
    connected: true,
    display_name: "Ada",
    error: false,
    sync: syncState,
  };
}

function reply(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

function stubReplies(replies: Response[], fallback?: Response) {
  const fetch = vi.fn(async () => replies.shift() ?? fallback!.clone());
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

function setup() {
  const scope = effectScope();
  const state = scope.run(() => usePluginConnections())!;
  return { state, scope };
}

describe("usePluginConnections", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    tasks.splice(0);
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("shares concurrent status reads and refreshes the next completed read", async () => {
    let finish!: (value: Response) => void;
    const fetch = vi.fn(
      () =>
        new Promise<Response>((resolve) => {
          finish = resolve;
        }),
    );
    vi.stubGlobal("fetch", fetch);
    const first = setup();
    const second = setup();
    const pending = Promise.all([first.state.load(), second.state.load()]);
    expect(fetch).toHaveBeenCalledOnce();
    finish(reply({ connections: [connection()] }));
    await pending;
    expect(first.state.states[KEY].connected).toBe(true);
    expect(second.state.states[KEY].connected).toBe(true);
    fetch.mockResolvedValue(reply({ connections: [connection(null)] }));
    await first.state.load();
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(first.state.states[KEY].sync).toBeNull();
    first.scope.stop();
    second.scope.stop();
  });

  it("loads connection state from the host, not from the plugin", async () => {
    const fetch = stubReplies([reply({ connections: [connection()] })]);
    const { state } = setup();
    await state.load();
    expect(fetch).toHaveBeenCalledWith("/api/connections/plugins", {
      credentials: "include",
    });
    expect(state.states[KEY]).toMatchObject({ connected: true });
    expect(state.loading.value).toBe(false);
    expect(state.loadError.value).toBeNull();
  });

  it("reports a failed load and recovers on retry", async () => {
    stubReplies([
      reply({ detail: "down" }, 503),
      reply({ connections: [connection()] }),
    ]);
    const { state } = setup();
    await state.load();
    expect(state.loadError.value).toBeTruthy();
    expect(state.loading.value).toBe(false);
    await state.load();
    expect(state.loadError.value).toBeNull();
    expect(state.states[KEY]).toBeDefined();
  });

  it("starts the sync on the host and polls until it finishes", async () => {
    const fetch = stubReplies([
      reply({ connections: [connection()] }),
      reply({ sync: sync({ running: true }) }),
      reply({ connections: [connection(sync({ running: true }))] }),
      reply({
        connections: [
          connection(
            sync({ status: "completed", last_summary: "12 games saved" }),
          ),
        ],
      }),
    ]);
    const { state } = setup();
    await state.load();
    const done = state.sync(KEY, "Epic Games");
    await vi.advanceTimersByTimeAsync(0);
    expect(state.isSyncing(KEY)).toBe(true);
    expect(tasks[0]).toMatchObject({
      label: "Syncing Epic Games library",
      status: "running",
    });
    await vi.advanceTimersByTimeAsync(SYNC_POLL_MS * 2);
    await done;
    const calls = (fetch.mock.calls as unknown as [string, RequestInit?][]).map(
      ([url, init]) => `${init?.method ?? "GET"} ${url}`,
    );
    expect(calls).toEqual([
      "GET /api/connections/plugins",
      "POST /api/connections/plugins/official.epic-games/epic-games/sync",
      "GET /api/connections/plugins",
      "GET /api/connections/plugins",
    ]);
    expect(state.isSyncing(KEY)).toBe(false);
    expect(tasks[0]).toMatchObject({
      status: "done",
      detail: "12 games saved",
    });
  });

  it("shows a failed sync with the host's reason", async () => {
    stubReplies([
      reply({ connections: [connection()] }),
      reply({ sync: sync({ running: true }) }),
      reply({
        connections: [
          connection(
            sync({
              status: "failed",
              last_summary: "Epic is rate limiting requests.",
            }),
          ),
        ],
      }),
    ]);
    const { state } = setup();
    await state.load();
    const done = state.sync(KEY, "Epic Games");
    await vi.advanceTimersByTimeAsync(SYNC_POLL_MS);
    await done;
    expect(tasks[0]).toMatchObject({
      status: "error",
      detail: "Epic is rate limiting requests.",
    });
    expect(state.isSyncing(KEY)).toBe(false);
  });

  it("surfaces a refused start and allows trying again", async () => {
    stubReplies([
      reply({ connections: [connection()] }),
      reply({ detail: "Background permission is unavailable." }, 409),
      reply({ sync: sync({ status: "completed" }) }),
    ]);
    const { state } = setup();
    await state.load();
    await state.sync(KEY, "Epic Games");
    expect(tasks[0]).toMatchObject({
      status: "error",
      detail: "Background permission is unavailable.",
    });
    expect(state.isSyncing(KEY)).toBe(false);
    await state.sync(KEY, "Epic Games");
    expect(tasks[1].status).toBe("done");
  });

  it("does not start a second sync while one is running", async () => {
    stubReplies(
      [],
      reply({ connections: [connection(sync({ running: true }))] }),
    );
    const { state, scope } = setup();
    await state.load();
    expect(state.isSyncing(KEY)).toBe(true);
    await state.sync(KEY, "Epic Games");
    expect(tasks).toHaveLength(0);
    scope.stop();
  });

  it("keeps polling a sync that was already running when the page opened", async () => {
    stubReplies([
      reply({ connections: [connection(sync({ running: true }))] }),
      reply({
        connections: [
          connection(sync({ status: "completed", last_summary: "done" })),
        ],
      }),
    ]);
    const { state } = setup();
    await state.load();
    expect(state.isSyncing(KEY)).toBe(true);
    await vi.advanceTimersByTimeAsync(SYNC_POLL_MS);
    expect(state.isSyncing(KEY)).toBe(false);
  });

  it("reports lost contact when polling keeps failing", async () => {
    stubReplies(
      [
        reply({ connections: [connection()] }),
        reply({ sync: sync({ running: true }) }),
      ],
      reply({}, 500),
    );
    const { state } = setup();
    await state.load();
    const done = state.sync(KEY, "Epic Games");
    await vi.advanceTimersByTimeAsync(SYNC_POLL_MS * 4);
    await done;
    expect(tasks[0].status).toBe("error");
    expect(tasks[0].detail).toContain("Lost contact");
  });
});
