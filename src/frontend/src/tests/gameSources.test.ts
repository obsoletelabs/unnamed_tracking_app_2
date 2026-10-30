// @vitest-environment happy-dom
import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import GameSourcesPanel from "../components/game/GameSourcesPanel.vue";
import * as identity from "../services/gameIdentity";
import type {
  CounterMember,
  CounterView,
  IdentitySources,
  SourceObservation,
} from "../services/gameIdentity";
import { preferenceChanges, summarizeSources } from "../utils/gameSources";

vi.mock("../services/gameIdentity", async (original) => ({
  ...(await original<typeof import("../services/gameIdentity")>()),
  fetchIdentitySources: vi.fn(),
  setCounterPriority: vi.fn(async () => {}),
}));

vi.mock("../services/gameFieldSources", async (original) => ({
  ...(await original<typeof import("../services/gameFieldSources")>()),
  fetchFieldSources: vi.fn(async () => ({})),
}));

const observation = (
  over: Partial<SourceObservation> & { observation_id: string },
): SourceObservation => ({
  game_id: "alan",
  source: "playnite",
  source_kind: "host",
  scope: "library:1",
  connection_label: "Playnite",
  disconnected: false,
  external_id: "x",
  title: "Alan Wake 2",
  platform: "PC (Windows)",
  edition: null,
  availability: "available",
  unavailable_since: null,
  last_observed_at: Math.floor(Date.now() / 1000) - 3 * 86400,
  revision: 1,
  facts: {},
  ...over,
});

const member = (
  over: Partial<CounterMember> & { observation_id: string },
): CounterMember => ({
  source: "playnite",
  value: 3600,
  observed_at: 1,
  priority: 100,
  selected: false,
  available: true,
  ...over,
});

/** Playnite and the Epic plugin report one Epic copy: one shared playtime counter. */
function data(
  members?: [Partial<CounterMember>, Partial<CounterMember>],
): IdentitySources {
  const [a, b] = members ?? [{ selected: true }, {}];
  const counter: CounterView = {
    counter_id: "c1",
    game_id: "alan",
    metric: "playtime",
    raw: 3600,
    effective: 3600,
    selected_source: "playnite",
    members: [
      member({ observation_id: "o-pn", ...a }),
      member({ observation_id: "o-ep", source: "epic-games", ...b }),
    ],
    conflicts: [],
    adjustment: null,
  };
  return {
    game_id: "alan",
    umbrella_id: "alan",
    titles: {},
    versions: [
      {
        game_id: "alan",
        title: "Alan Wake 2",
        platform: "PC (Windows)",
        source: "Epic",
        playnite_guid: null,
      },
    ],
    observations: [
      observation({
        observation_id: "o-pn",
        facts: {
          playtime_seconds: 41 * 3600,
          last_played_at: 1760000000,
          achievements_unlocked: 3,
          achievements_total: 50,
        },
      }),
      observation({
        observation_id: "o-ep",
        source: "epic-games",
        source_kind: "plugin",
        connection_label: "Epic Games",
        facts: { playtime_seconds: 39 * 3600, owned: true },
      }),
    ],
    relationships: [],
    decisions: [],
    counters: { playtime: [counter] },
  };
}

describe("summarising a game's sources", () => {
  it("names each source, what it contributes and which copy it covers", () => {
    const [playnite, epic] = summarizeSources(data());
    expect(playnite.name).toBe("Playnite");
    expect(playnite.contributes).toEqual([
      "Playtime 41h",
      expect.stringContaining("Last played"),
      "Achievements 3 / 50",
      "Title and details",
    ]);
    expect(playnite.copies).toEqual([
      { gameId: "alan", name: "Epic · PC (Windows)" },
    ]);
    expect(epic.name).toBe("Epic Games plugin");
    expect(epic.contributes).toContain("Owned");
  });

  it("marks the winning source preferred and offers the other", () => {
    const [playnite, epic] = summarizeSources(data());
    expect(playnite).toMatchObject({ preferred: true, canPrefer: false });
    expect(epic).toMatchObject({ preferred: false, canPrefer: true });
  });

  it("has nothing to choose when only one source reports a counter", () => {
    const single = data();
    single.counters.playtime[0].members.pop();
    const [playnite] = summarizeSources(single);
    expect(playnite).toMatchObject({ preferred: false, canPrefer: false });
  });

  it("gives the preferred source the winning priority and resets the rest", () => {
    expect(preferenceChanges(data(), "epic-games")).toEqual([
      { counterId: "c1", observationId: "o-ep", priority: 10 },
    ]);
    const preferred = data([{ priority: 100 }, { priority: 10 }]);
    expect(preferenceChanges(preferred, "playnite")).toEqual([
      { counterId: "c1", observationId: "o-pn", priority: 10 },
      { counterId: "c1", observationId: "o-ep", priority: 100 },
    ]);
    expect(preferenceChanges(preferred, "epic-games")).toEqual([]);
  });
});

describe("the Sources panel", () => {
  beforeEach(() => vi.clearAllMocks());
  const mountPanel = () =>
    mount(GameSourcesPanel, { props: { gameId: "alan" } });

  it("shows the sources and lets the person prefer another one", async () => {
    vi.mocked(identity.fetchIdentitySources).mockResolvedValue(data());
    const wrapper = mountPanel();
    await flushPromises();
    expect(wrapper.text()).toContain("Playnite");
    expect(wrapper.text()).toContain("Epic Games plugin");
    expect(wrapper.text()).toContain("Preferred for playtime");
    await wrapper.get("button.prefer").trigger("click");
    await flushPromises();
    expect(identity.setCounterPriority).toHaveBeenCalledWith("c1", "o-ep", 10);
    expect(wrapper.emitted("changed")).toHaveLength(1);
    expect(identity.fetchIdentitySources).toHaveBeenCalledTimes(2);
  });

  it("shows a loading state, then an error with a working Retry", async () => {
    vi.mocked(identity.fetchIdentitySources)
      .mockRejectedValueOnce(new Error("Server unavailable"))
      .mockResolvedValue(data());
    const wrapper = mountPanel();
    expect(wrapper.text()).toContain("Loading sources");
    await flushPromises();
    expect(wrapper.text()).toContain("Server unavailable");
    await wrapper.get("button").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Epic Games plugin");
  });

  it("stays out of the way for a game nothing has reported", async () => {
    vi.mocked(identity.fetchIdentitySources).mockResolvedValue({
      ...data(),
      observations: [],
      counters: {},
    });
    const wrapper = mountPanel();
    await flushPromises();
    expect(wrapper.find(".sources").exists()).toBe(false);
  });

  it("keeps the page usable and says so when saving the choice fails", async () => {
    vi.mocked(identity.fetchIdentitySources).mockResolvedValue(data());
    vi.mocked(identity.setCounterPriority).mockRejectedValueOnce(
      new Error("Could not save"),
    );
    const wrapper = mountPanel();
    await flushPromises();
    await wrapper.get("button.prefer").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Could not save");
    expect(wrapper.text()).toContain("Epic Games plugin");
  });
});
